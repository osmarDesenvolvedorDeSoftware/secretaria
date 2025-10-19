from __future__ import annotations

import argparse
import pathlib
import secrets
from typing import Dict

ENV_KEYS = {
    "WHATICKET_JWT_PASSWORD": "token_whaticket",
    "WHATSAPP_BEARER_TOKEN": "token_whatsapp",
    "GEMINI_API_KEY": "token_llm",
    "PANEL_JWT_SECRET": "panel_secret",
}


def generate_token(name: str) -> str:
    prefix = name.upper()
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def load_env(path: pathlib.Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if not line or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(path: pathlib.Path, original: str, updates: Dict[str, str]) -> None:
    lines = original.splitlines()
    updated_keys = set()
    new_lines = []
    for line in lines:
        if not line or line.strip().startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key, sep, value = line.partition("=")
        key_stripped = key.strip()
        if key_stripped in updates:
            new_lines.append(f"{key_stripped}{sep}{updates[key_stripped]}")
            updated_keys.add(key_stripped)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")


def rotate(path: pathlib.Path) -> Dict[str, str]:
    env_text = path.read_text() if path.exists() else ""
    updates: Dict[str, str] = {}
    for key, name in ENV_KEYS.items():
        updates[key] = generate_token(name)
    write_env(path, env_text, updates)
    return updates


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera novos tokens e atualiza o arquivo .env")
    parser.add_argument(
        "--env",
        default=".env",
        help="Caminho para o arquivo .env (padrão: .env na raiz do projeto)",
    )
    args = parser.parse_args()
    env_path = pathlib.Path(args.env).resolve()
    updates = rotate(env_path)
    for key, value in updates.items():
        print(f"{key} atualizado: {value[:8]}...")


if __name__ == "__main__":
    main()
