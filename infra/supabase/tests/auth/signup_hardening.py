#!/usr/bin/env python3
"""Auth 設定の回帰テスト — パスワード付き signup で「確認前のセッション」「事前乗っ取り」ができないこと。

ログインはマジックリンク / 6桁コードのみ（パスワードは使わない）だが、POST /auth/v1/signup {email, password}
は anon key で誰でも呼べる。次の 2 点を確認する（標準ライブラリのみ。キーは表示しない）:

  1. config.toml: [auth.email] enable_confirmations = true、匿名ログイン無効（静的チェック）
  2. 起動中のローカル Auth（--static-only で省略）:
     a. 他人のメールアドレスで password 付き signup → 応答に access_token が無い（メール所有の確認前にログインできない）
     b. Mailpit が動いていれば事前乗っ取りのシナリオ全体:
        攻撃者が password 付き signup → 本人が signInWithOtp（/auth/v1/otp）→ 届いた 6桁コードで確認
        → 攻撃者のパスワードでは password grant できない（on_auth_user_email_verified トリガー）
     作成したユーザーは最後に DATABASE_URL（既定: ローカル）で削除する。

使い方:
  python3 infra/supabase/tests/auth/signup_hardening.py               # 静的チェック + ローカル Auth
  python3 infra/supabase/tests/auth/signup_hardening.py --static-only # config.toml だけ
環境変数: SUPABASE_URL / SUPABASE_ANON_KEY（未指定なら `supabase status --workdir infra -o json`）、
          DATABASE_URL（既定 postgresql://postgres:postgres@127.0.0.1:54322/postgres）、MAILPIT_URL（既定 http://127.0.0.1:54324）

config.toml の変更はローカルスタックの再起動（supabase stop && supabase start）まで Auth に反映されない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / "infra" / "supabase" / "config.toml"
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("ok    " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def static_checks() -> None:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    auth = config.get("auth", {})
    email = auth.get("email", {})
    check(email.get("enable_confirmations") is True, "config.toml: [auth.email] enable_confirmations = true")
    check(auth.get("enable_anonymous_sign_ins") is False, "config.toml: [auth] enable_anonymous_sign_ins = false")


def request(
    method: str, url: str, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            status = res.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {"raw": raw.decode(errors="replace")[:200]}
    return status, data if isinstance(data, dict) else {"data": data}


def local_keys() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    anon = os.environ.get("SUPABASE_ANON_KEY")
    if url and anon:
        return url.rstrip("/"), anon
    out = subprocess.run(
        ["supabase", "status", "--workdir", str(ROOT / "infra"), "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    status = json.loads(out)
    return status["API_URL"].rstrip("/"), status["ANON_KEY"]


def wait_for_code(mailpit: str, email: str, since: float) -> str | None:
    """since 以降に email 宛てに届いたメールから 6桁コードを取り出す（攻撃者の signup 時の確認メールは無効化済みなので除く）。"""
    for _ in range(40):
        _, listing = request("GET", f"{mailpit}/api/v1/messages?limit=50")
        for msg in listing.get("messages", []):
            to = [t.get("Address", "").lower() for t in msg.get("To", [])]
            created = msg.get("Created", "")
            if email in to and created and datetime.fromisoformat(created).timestamp() >= since - 0.5:
                _, detail = request("GET", f"{mailpit}/api/v1/message/{msg['ID']}")
                found = re.search(r"\b(\d{6})\b", detail.get("Text", ""))
                if found:
                    return found.group(1)
        time.sleep(0.5)
    return None


def live_checks() -> None:
    api, anon = local_keys()
    auth = f"{api}/auth/v1"
    headers = {"apikey": anon, "Authorization": f"Bearer {anon}"}
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    mailpit = os.environ.get("MAILPIT_URL", "http://127.0.0.1:54324").rstrip("/")
    victim = f"signup-hardening-{uuid.uuid4().hex[:10]}@example.test"
    password = f"attacker-{uuid.uuid4().hex[:12]}"
    try:
        # a. 他人のメールアドレスで password 付き signup → セッションは発行されない
        status, body = request("POST", f"{auth}/signup", {"email": victim, "password": password}, headers)
        check(
            not body.get("access_token"),
            f"password 付き signup の応答に access_token が無い（HTTP {status}）"
            + (
                ""
                if not body.get("access_token")
                else " — enable_confirmations が反映されていない（スタック再起動が必要）"
            ),
        )
        status, body = request(
            "POST", f"{auth}/token?grant_type=password", {"email": victim, "password": password}, headers
        )
        check(not body.get("access_token"), f"確認前は攻撃者のパスワードでログインできない（HTTP {status}）")

        # b. 事前乗っ取りのシナリオ（Mailpit が必要）
        mailpit_status, _ = request("GET", f"{mailpit}/api/v1/messages?limit=1")
        if mailpit_status != 200:
            print("skip  Mailpit に接続できないため、本人のメール確認後のシナリオは省略")
            return
        time.sleep(1.5)  # 確認メールの再送間隔（[auth.email] max_frequency）を空ける
        since = time.time()
        status, _ = request("POST", f"{auth}/otp", {"email": victim, "create_user": True}, headers)
        check(status == 200, f"本人が signInWithOtp（/otp）でログインメールを要求（HTTP {status}）")
        code = wait_for_code(mailpit, victim, since)
        check(code is not None, "本人にログイン用の 6桁コードが届く")
        if code is None:
            return
        status, session = request("POST", f"{auth}/verify", {"type": "email", "email": victim, "token": code}, headers)
        check(bool(session.get("access_token")), f"本人は 6桁コードでログインできる（HTTP {status}）")
        status, body = request(
            "POST", f"{auth}/token?grant_type=password", {"email": victim, "password": password}, headers
        )
        check(
            not body.get("access_token"),
            f"本人の確認後も、攻撃者が signup 時に設定したパスワードではログインできない（HTTP {status}）",
        )
    finally:
        subprocess.run(
            ["psql", database_url, "-X", "-q", "-c", f"delete from auth.users where email = '{victim}'"],
            check=False,
            capture_output=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--static-only", action="store_true", help="config.toml の静的チェックだけを行う")
    args = parser.parse_args()
    static_checks()
    if not args.static_only:
        live_checks()
    if failures:
        print(f"\n{len(failures)} 件失敗しました", file=sys.stderr)
        return 1
    print("\nすべて成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
