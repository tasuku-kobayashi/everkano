#!/usr/bin/env python3
"""ペルソナYAML（packages/personas/*.yaml）とシードの整合性を検証する。

使い方（packages/personas ディレクトリで実行）:
    pnpm validate
    # = uv run --frozen --project ../../apps/api python scripts/validate_personas.py

チェック内容:
  1. 必須フィールドと型（BRIEF §2.7 のスキーマ）
  2. key == ファイル名、handle の形式（^[a-z0-9_.]{2,30}$）と一意性、name の一意性
  3. age が 20 以上の整数（全キャラ成人）
  4. speech.examples が 5 件以上、memory_focus が 3〜5 件、avatar が https URL
  5. 未成年を想起させる語・20歳未満の年齢表記が YAML / feed.yaml / seed.sql に含まれないこと
  6. infra/supabase/seed.sql に各キャラの行（handle / name / persona_key / avatar / bio）があること
  7. seed.sql が generate_seed.py の生成結果と一致すること（手編集・再生成忘れの検出）
  8. API の Pydantic モデル（app.services.persona.Persona / EngineProfile）で読み込めること
  9. engine セクション（キャラクターエンジン v1.0）の追加規則（scripts/engine_checks.py）:
     routine の重なり・すき間、タグ語彙、SEASONAL_KEYS、E2/E3/Gate #1 の語、自発メッセージの文言 など。
     apps/api/tests/fixtures/personas/test_persona.yaml の engine も同じ規則で検証する
     （API のモジュールを import するため、apps/api の uv 環境で実行する）
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# app.services.persona など API のモデルで検証する（apps/api の uv 環境で実行すること）
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))

import engine_checks  # 同じディレクトリのモジュール（sys.path に追加済み）
import generate_seed
from app.services.persona import Persona, PersonaLoadError, load_persona_file

API_ROOT = generate_seed.REPO_ROOT / "apps" / "api"
FIXTURE_PERSONA = API_ROOT / "tests" / "fixtures" / "personas" / "test_persona.yaml"

HANDLE_RE = re.compile(r"^[a-z0-9_.]{2,30}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PROFILE_MIN_CHARS = 350  # 500〜800トークン相当の目安（日本語）。短すぎ・長すぎを検出する
PROFILE_MAX_CHARS = 1600
MIN_AGE = 20
MIN_EXAMPLES = 5

STRING_FIELDS = (
    "key",
    "name",
    "handle",
    "archetype",
    "avatar",
    "bio",
    "profile",
    "schedule_pattern",
    "greeting",
    "comment_style",
)
SPEECH_STRING_FIELDS = ("tone", "sentence_length", "emoji", "first_person", "second_person")
RELATIONSHIP_FIELDS = ("initial", "progression")

# 未成年を想起させる語（正規化後に部分一致で検出）。moderation.py（Gate #1）と同じ観点。
MINOR_TERMS = (
    "小学生",
    "中学生",
    "高校生",
    "女子高生",
    "女子中学生",
    "ロリ",
    "幼女",
    "児童",
    "未成年",
    "ランドセル",
    "制服",
    "学生服",
    "セーラー服",
    "スクール水着",
)
# ASCII の略語は単語境界つきで検出（URL 等は除外してから検査する）
MINOR_ASCII_RE = re.compile(r"(?<![a-z])(jk|jc|js)(?![a-z])")
# 20歳未満の年齢表記（算用数字・漢数字）。「312歳」「三百十二歳」は対象外
UNDERAGE_DIGIT_RE = re.compile(r"(?<![0-9])(1[0-9]|[0-9])\s*(歳|才)")
UNDERAGE_KANJI_RE = re.compile(
    r"(?<![〇一二三四五六七八九十百千万])(十[一二三四五六七八九]?|[一二三四五六七八九])\s*(歳|才)"
)
URL_RE = re.compile(r"https?://\S+")


def katakana_to_hiragana(text: str) -> str:
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text)


def normalize(text: str) -> str:
    """moderation.py と同じ方針で正規化する（NFKC → 小文字 → カタカナをひらがなへ）。"""
    return katakana_to_hiragana(unicodedata.normalize("NFKC", text).lower())


NORMALIZED_MINOR_TERMS = tuple(normalize(t) for t in MINOR_TERMS)


def iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in iter_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in iter_strings(v)]
    return []


def find_sensitive(text: str) -> list[str]:
    """未成年を想起させる語・20歳未満の年齢表記を返す。"""
    text = URL_RE.sub(" ", text)
    norm = normalize(text)
    hits = [term for term, n in zip(MINOR_TERMS, NORMALIZED_MINOR_TERMS, strict=True) if n in norm]
    hits += [m.group(0) for m in MINOR_ASCII_RE.finditer(norm)]
    nfkc = unicodedata.normalize("NFKC", text)
    hits += [m.group(0) for m in UNDERAGE_DIGIT_RE.finditer(nfkc)]
    hits += [m.group(0) for m in UNDERAGE_KANJI_RE.finditer(nfkc)]
    return hits


def validate_persona(path: Path, data: Any, errors: list[str], warnings: list[str]) -> None:
    name = path.name
    if not isinstance(data, dict):
        errors.append(f"{name}: トップレベルがマッピングではありません")
        return

    for f in STRING_FIELDS:
        v = data.get(f)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"{name}: {f} は必須の文字列です")

    key = data.get("key")
    if isinstance(key, str):
        if key != path.stem:
            errors.append(f"{name}: key（{key}）とファイル名（{path.stem}）が一致しません")
        if not KEY_RE.match(key):
            errors.append(f"{name}: key は英小文字・数字・_ のみ（{key!r}）")

    age = data.get("age")
    if not isinstance(age, int) or isinstance(age, bool):
        errors.append(f"{name}: age は整数で指定してください（{age!r}）")
    elif age < MIN_AGE:
        errors.append(f"{name}: age は {MIN_AGE} 以上にしてください（全キャラ成人）: {age}")

    handle = data.get("handle")
    if isinstance(handle, str) and not HANDLE_RE.match(handle):
        errors.append(f"{name}: handle が ^[a-z0-9_.]{{2,30}}$ に一致しません（{handle!r}）")

    avatar = data.get("avatar")
    if isinstance(avatar, str):
        if not avatar.startswith("https://"):
            errors.append(f"{name}: avatar は https:// のURL（またはストレージのキー）にしてください")
        if "api.dicebear.com" in avatar and isinstance(handle, str) and f"seed={handle}" not in avatar:
            warnings.append(f"{name}: avatar の seed が handle と一致していません")

    profile = data.get("profile")
    if isinstance(profile, str):
        n = len(profile.strip())
        if not PROFILE_MIN_CHARS <= n <= PROFILE_MAX_CHARS:
            errors.append(
                f"{name}: profile は {PROFILE_MIN_CHARS}〜{PROFILE_MAX_CHARS} 文字を目安にしてください（{n}文字）"
            )

    schedule = data.get("schedule_pattern")
    if isinstance(schedule, str) and not ("平日" in schedule and "休日" in schedule):
        errors.append(f"{name}: schedule_pattern に「平日」「休日」の行を書いてください")

    speech = data.get("speech")
    if not isinstance(speech, dict):
        errors.append(f"{name}: speech はマッピングで必須です")
    else:
        for f in SPEECH_STRING_FIELDS:
            v = speech.get(f)
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{name}: speech.{f} は必須の文字列です")
        ng = speech.get("ng_words")
        if not isinstance(ng, list) or not all(isinstance(w, str) and w for w in ng):
            errors.append(f"{name}: speech.ng_words は文字列のリストで必須です")
        examples = speech.get("examples")
        if not isinstance(examples, list) or not all(isinstance(e, str) and e.strip() for e in examples):
            errors.append(f"{name}: speech.examples は文字列のリストで必須です")
        elif len(examples) < MIN_EXAMPLES:
            errors.append(f"{name}: speech.examples は {MIN_EXAMPLES} 件以上必要です（{len(examples)}件）")
        elif isinstance(ng, list):
            for e in examples:
                for w in ng:
                    if isinstance(w, str) and w and normalize(w) in normalize(e):
                        errors.append(f"{name}: speech.examples に ng_words の「{w}」が含まれています: {e}")

    relationship = data.get("relationship")
    if not isinstance(relationship, dict):
        errors.append(f"{name}: relationship はマッピングで必須です")
    else:
        for f in RELATIONSHIP_FIELDS:
            v = relationship.get(f)
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{name}: relationship.{f} は必須の文字列です")

    focus = data.get("memory_focus")
    if not isinstance(focus, list) or not all(isinstance(x, str) and x.strip() for x in focus):
        errors.append(f"{name}: memory_focus は文字列のリストで必須です")
    elif not 3 <= len(focus) <= 5:
        errors.append(f"{name}: memory_focus は 3〜5 件にしてください（{len(focus)}件）")

    moderation_reply = data.get("moderation_reply")
    if moderation_reply is None:
        warnings.append(f"{name}: moderation_reply が未設定です（既定文が使われます）")
    elif not isinstance(moderation_reply, str) or not moderation_reply.strip():
        errors.append(f"{name}: moderation_reply は文字列にしてください")

    for s in iter_strings(data):
        for hit in find_sensitive(s):
            errors.append(f"{name}: 未成年を想起させる表現「{hit}」が含まれています")


def load_model(path: Path, errors: list[str]) -> Persona | None:
    """API と同じ Pydantic モデルで読み込む（engine は extra="forbid" で未知のキーもエラー）。"""
    try:
        return load_persona_file(path)
    except PersonaLoadError as exc:
        errors.append(f"API モデルでの検証に失敗: {exc}")
        return None


def parse_seed_character_rows(sql: str) -> list[tuple[str, str, str, str]]:
    """seed.sql の characters 行から (id, handle, name, persona_key) を取り出す。"""
    row_re = re.compile(
        r"\(\s*'(?P<id>00000000-0000-4000-8000-[0-9a-f]{12})',\s*'(?P<handle>[^']*)',"
        r"\s*'(?P<name>(?:[^']|'')*)',\s*'(?P<key>[^']*)',"
    )
    return [(m["id"], m["handle"], m["name"].replace("''", "'"), m["key"]) for m in row_re.finditer(sql)]


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    paths = sorted(generate_seed.PACKAGE_DIR.glob("*.yaml"))
    if not paths:
        print("ERROR: packages/personas/*.yaml が見つかりません", file=sys.stderr)
        return 1

    personas: dict[str, dict[str, Any]] = {}
    models: list[Persona] = []
    reports: list[engine_checks.EngineReport] = []
    handles: dict[str, str] = {}
    names: dict[str, str] = {}
    for path in paths:
        try:
            data = generate_seed.load_yaml(path)
        except Exception as exc:  # YAML 構文エラーはファイル名つきで報告する
            errors.append(f"{path.name}: YAML を読み込めません: {exc}")
            continue
        validate_persona(path, data, errors, warnings)
        if not isinstance(data, dict):
            continue
        model = load_model(path, errors)
        if model is not None:
            models.append(model)
            reports.append(engine_checks.check_engine(model, path.name, errors, warnings))
        personas[path.stem] = data
        handle = data.get("handle")
        if isinstance(handle, str):
            if handle in handles:
                errors.append(f"{path.name}: handle {handle!r} が {handles[handle]} と重複しています")
            handles[handle] = path.name
        pname = data.get("name")
        if isinstance(pname, str):
            if pname in names:
                errors.append(f"{path.name}: name {pname!r} が {names[pname]} と重複しています")
            names[pname] = path.name

    engine_checks.check_birthdays(models, warnings)

    # テスト用ペルソナ（apps/api/tests/fixtures）の engine も同じ規則で検証する（他モジュールのテストが使う）
    fixture = load_model(FIXTURE_PERSONA, errors)
    if fixture is not None:
        fixture_where = str(FIXTURE_PERSONA.relative_to(generate_seed.REPO_ROOT))
        engine_checks.check_engine(fixture, fixture_where, errors, warnings)

    # feed.yaml（投稿・コメント）の表現チェック
    try:
        feed = generate_seed.load_yaml(generate_seed.FEED_PATH)
        for s in iter_strings(feed):
            for hit in find_sensitive(s):
                errors.append(f"seed/feed.yaml: 未成年を想起させる表現「{hit}」が含まれています: {s[:40]}")
    except Exception as exc:
        errors.append(f"seed/feed.yaml を読み込めません: {exc}")

    # seed.sql との突き合わせ
    seed_path = generate_seed.SEED_SQL_PATH
    rel_seed = seed_path.relative_to(generate_seed.REPO_ROOT)
    if not seed_path.exists():
        errors.append(f"{rel_seed} がありません（seed:generate を実行してください）")
    else:
        sql = seed_path.read_text(encoding="utf-8")
        rows = parse_seed_character_rows(sql)
        by_key = {key: (cid, handle, name) for cid, handle, name, key in rows}
        if len(by_key) != len(rows):
            errors.append(f"{rel_seed}: persona_key が重複しています")
        ids = [cid for cid, _, _, _ in rows]
        if len(ids) != len(set(ids)):
            errors.append(f"{rel_seed}: characters の id が重複しています")
        for key, data in personas.items():
            if key not in by_key:
                errors.append(f"{rel_seed}: persona_key {key!r} の characters 行がありません")
                continue
            _, seed_handle, seed_name = by_key[key]
            if seed_handle != data.get("handle"):
                errors.append(f"{rel_seed}: {key} の handle が YAML と一致しません（{seed_handle}）")
            if seed_name != data.get("name"):
                errors.append(f"{rel_seed}: {key} の name が YAML と一致しません（{seed_name}）")
            for f in ("avatar", "bio"):
                v = data.get(f)
                if isinstance(v, str) and sql_literal(v.strip()) not in sql:
                    errors.append(f"{rel_seed}: {key} の {f} が YAML と一致しません")
        for key in sorted(set(by_key) - set(personas)):
            errors.append(f"{rel_seed}: persona_key {key!r} に対応する YAML がありません")
        # system_prompt 末尾の固定の「制約」（未成年表現の禁止を明記した行）は検査対象外
        scannable = sql
        for line in generate_seed.PROMPT_CONSTRAINTS:
            scannable = scannable.replace(line, "")
        for hit in find_sensitive(scannable):
            errors.append(f"{rel_seed}: 未成年を想起させる表現「{hit}」が含まれています")

        try:
            expected = generate_seed.render()
        except generate_seed.SeedError as exc:
            errors.append(f"seed データの不備: {exc}")
        else:
            if expected != sql:
                errors.append(
                    f"{rel_seed} が生成結果と一致しません。"
                    "`pnpm --filter @everkano/personas seed:generate` で再生成してください（手編集は不可）"
                )

    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"\n{len(errors)} error(s)", file=sys.stderr)
        return 1
    for report in reports:
        print(f"  {report.summary()}")
    print(f"OK: {len(personas)} personas validated ({', '.join(sorted(personas))}); {rel_seed} is in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
