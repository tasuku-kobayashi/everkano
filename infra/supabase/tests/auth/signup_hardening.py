#!/usr/bin/env python3
"""Auth 設定の回帰テスト — パスワードを使った乗っ取りができないこと、ログインメールの形式。

ログインはマジックリンク / 6桁コードのみ（パスワードは使わない）だが、POST /auth/v1/signup {email, password}
は anon key で誰でも呼べ、PUT /auth/v1/user {password} はアクセストークンだけで呼べる。
次を確認する（標準ライブラリのみ。キーは表示しない）:

  1. 静的チェック（CI でも実行）:
     - config.toml: [auth.email] enable_confirmations = true、匿名ログイン無効、secure_password_change = true、
       otp_length = 6、otp_expiry <= 900、パスワード変更の通知メールが有効、
       additional_redirect_urls がクエリ付き（?next=）の /auth/callback を許可する
     - templates/magic_link.html（magic_link / confirmation 共通）: /auth/confirm へのリンク（token_hash・type=email・
       redirect_to）と 6桁コード、コードを他人に教えない旨の注意書き
  2. 起動中のローカル Auth（--static-only で省略）:
     a. 他人のメールアドレスで password 付き signup → 応答に access_token が無い（メール所有の確認前にログインできない）
     b. Mailpit が動いていれば事前乗っ取りのシナリオ全体:
        攻撃者が password 付き signup → 本人が signInWithOtp（/auth/v1/otp, emailRedirectTo に next 付き）→
        届いたメールのリンクが redirect_to で next を運ぶ → 6桁コードで確認
        → 攻撃者のパスワードでは password grant できない（on_auth_user_email_verified トリガー）
     c. b でログインしたセッションのアクセストークンで PUT /auth/v1/user {password} → そのパスワードでは
        password grant できない（on_auth_user_password_update トリガー。盗まれたトークンからの恒久的な乗っ取り対策）
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
import html
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
SUPABASE_DIR = ROOT / "infra" / "supabase"
CONFIG = SUPABASE_DIR / "config.toml"
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
# 6桁コードとマジックリンクの有効期限の上限（秒）。docs/handover/supabase-auth.md と揃える
MAX_OTP_EXPIRY_SECONDS = 900
# ログインメールのリンク（Web の app/auth/confirm と一致させる）
CONFIRM_LINK = (
    "{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&redirect_to={{ .RedirectTo | urlquery }}"
)
# signInWithOtp の emailRedirectTo（Web の components/auth/login-form.tsx と一致させる）
LOGIN_NEXT_PATH = "/posts/00000000-0000-4000-8001-000000000901"

failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("ok    " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def template_path(section: dict[str, Any]) -> Path | None:
    """config.toml の content_path（supabase/ からの相対）を実ファイルのパスにする。"""
    content_path = section.get("content_path")
    if not isinstance(content_path, str) or not content_path.startswith("./supabase/"):
        return None
    return SUPABASE_DIR / content_path.removeprefix("./supabase/")


def static_checks() -> None:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    auth = config.get("auth", {})
    email = auth.get("email", {})
    check(email.get("enable_confirmations") is True, "config.toml: [auth.email] enable_confirmations = true")
    check(auth.get("enable_anonymous_sign_ins") is False, "config.toml: [auth] enable_anonymous_sign_ins = false")
    check(email.get("secure_password_change") is True, "config.toml: [auth.email] secure_password_change = true")
    check(
        email.get("otp_length") == 6, "config.toml: [auth.email] otp_length = 6（ログイン画面のコード入力は 6 桁固定）"
    )
    otp_expiry = email.get("otp_expiry")
    check(
        isinstance(otp_expiry, int) and 0 < otp_expiry <= MAX_OTP_EXPIRY_SECONDS,
        f"config.toml: [auth.email] otp_expiry <= {MAX_OTP_EXPIRY_SECONDS}（6桁コードの総当たりに使える時間を短くする）",
    )

    notification = email.get("notification", {}).get("password_changed", {})
    notification_file = template_path(notification)
    check(
        notification.get("enabled") is True and notification_file is not None and notification_file.is_file(),
        "config.toml: [auth.email.notification.password_changed] が有効で、テンプレートのファイルがある",
    )

    redirect_urls = auth.get("additional_redirect_urls", [])
    check(
        bool(redirect_urls) and all(str(url).endswith("/auth/callback**") for url in redirect_urls),
        "config.toml: additional_redirect_urls はクエリ付き（/auth/callback?next=...）を許可する（末尾 /auth/callback**）",
    )

    templates = email.get("template", {})
    magic_link = template_path(templates.get("magic_link", {}))
    confirmation = template_path(templates.get("confirmation", {}))
    check(
        magic_link is not None and magic_link == confirmation,
        "config.toml: magic_link と confirmation のメールは同じテンプレート（新規ユーザーの初回は confirmation が送られる）",
    )
    body = magic_link.read_text(encoding="utf-8") if magic_link is not None and magic_link.is_file() else ""
    check(
        f'href="{CONFIRM_LINK}"' in body,
        "templates/magic_link.html: リンクが /auth/confirm?token_hash=..&type=email&redirect_to=<emailRedirectTo>（ログイン後の遷移先を運ぶ）",
    )
    check("{{ .Token }}" in body, "templates/magic_link.html: 6桁コード（{{ .Token }}）がある（ホーム画面の PWA 用）")
    check(
        "誰にも教えないでください" in body,
        "templates/magic_link.html: コード・リンクを他人に教えないよう注意書きがある（コードを聞き出す詐欺への対策）",
    )


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


def wait_for_mail(mailpit: str, email: str, since: float) -> tuple[str, str] | None:
    """since 以降に email 宛てに届いたメールから (6桁コード, /auth/confirm のリンク) を取り出す
    （攻撃者の signup 時の確認メールは無効化済みなので除く）。"""
    for _ in range(40):
        _, listing = request("GET", f"{mailpit}/api/v1/messages?limit=50")
        for msg in listing.get("messages", []):
            to = [t.get("Address", "").lower() for t in msg.get("To", [])]
            created = msg.get("Created", "")
            if email in to and created and datetime.fromisoformat(created).timestamp() >= since - 0.5:
                _, detail = request("GET", f"{mailpit}/api/v1/message/{msg['ID']}")
                found = re.search(r"\b(\d{6})\b", detail.get("Text", ""))
                link = re.search(r'href="([^"]*/auth/confirm\?[^"]+)"', detail.get("HTML", ""))
                if found:
                    return found.group(1), html.unescape(link.group(1)) if link else ""
        time.sleep(0.5)
    return None


def next_from_confirm_link(link: str) -> str | None:
    """メールのリンク（/auth/confirm?...&redirect_to=<emailRedirectTo>）から、Web が使う next を取り出す。"""
    redirect_to = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("redirect_to", [""])[0]
    return urllib.parse.parse_qs(urllib.parse.urlsplit(redirect_to).query).get("next", [None])[0]


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
        # Web の signInWithOtp と同じ emailRedirectTo（ログイン前に開こうとしていたページを next で運ぶ）
        site_url = str(tomllib.loads(CONFIG.read_text(encoding="utf-8"))["auth"]["site_url"]).rstrip("/")
        email_redirect_to = f"{site_url}/auth/callback?" + urllib.parse.urlencode({"next": LOGIN_NEXT_PATH})
        status, _ = request(
            "POST",
            f"{auth}/otp?" + urllib.parse.urlencode({"redirect_to": email_redirect_to}),
            {"email": victim, "create_user": True},
            headers,
        )
        check(status == 200, f"本人が signInWithOtp（/otp）でログインメールを要求（HTTP {status}）")
        mail = wait_for_mail(mailpit, victim, since)
        check(mail is not None, "本人にログイン用の 6桁コードが届く")
        if mail is None:
            return
        code, link = mail
        check(
            link.startswith(f"{site_url}/auth/confirm?token_hash=") and next_from_confirm_link(link) == LOGIN_NEXT_PATH,
            "ログインメールのリンクが /auth/confirm を指し、redirect_to でログイン後の遷移先（next）を運ぶ"
            + ("" if link else "（リンクが見つからない）"),
        )
        status, session = request("POST", f"{auth}/verify", {"type": "email", "email": victim, "token": code}, headers)
        check(bool(session.get("access_token")), f"本人は 6桁コードでログインできる（HTTP {status}）")
        status, body = request(
            "POST", f"{auth}/token?grant_type=password", {"email": victim, "password": password}, headers
        )
        check(
            not body.get("access_token"),
            f"本人の確認後も、攻撃者が signup 時に設定したパスワードではログインできない（HTTP {status}）",
        )

        # c. アクセストークンを入手した攻撃者が恒久的なパスワードを設定しようとする
        access_token = session.get("access_token")
        if not access_token:
            return
        persistent = f"persist-{uuid.uuid4().hex[:12]}"
        status, _ = request(
            "PUT",
            f"{auth}/user",
            {"password": persistent},
            {"apikey": anon, "Authorization": f"Bearer {access_token}"},
        )
        print(f"info  アクセストークンで PUT /auth/v1/user {{password}}（HTTP {status}）")
        status, body = request(
            "POST", f"{auth}/token?grant_type=password", {"email": victim, "password": persistent}, headers
        )
        check(
            not body.get("access_token"),
            f"アクセストークンで設定しようとしたパスワードではログインできない（HTTP {status}）",
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
