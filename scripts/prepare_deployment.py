"""Generate a new private Docker environment; never overwrite an existing file."""
import argparse
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def prepare(mode, output):
    template = ROOT / (".env.prod.example" if mode == "production" else ".env.docker-local.example")
    values = {key: secrets.token_hex(32) for key in
              ("SECRET_KEY", "METRICS_TOKEN", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "VISITOR_TEST_INVITE_CODE")}
    values["MYREID_ENV_FILE"] = Path(output).resolve().relative_to(ROOT).as_posix()
    if mode == "production":
        values["VISITOR_TEST_INVITE_CODE"] = ""
    content = template.read_text(encoding="utf-8")
    for key, value in values.items():
        content = re.sub(r"^" + key + r"=.*$", lambda _: f"{key}={value}", content, flags=re.M)
    with Path(output).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    # Windows access is governed by the containing directory's ACL.
    Path(output).chmod(0o600)
    return Path(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["local", "production"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / (".env.prod" if args.mode == "production" else ".env.docker-local")
    try:
        prepare(args.mode, output)
    except FileExistsError:
        parser.error("Output already exists; your private configuration was not modified")
    print(f"Created {output.name}; no secret values printed. See docs/NEW_MACHINE_DEPLOYMENT.md")
