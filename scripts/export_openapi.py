"""Write the API's OpenAPI (Swagger) spec to docs/openapi.json so it can be imported into
Postman / Swagger Editor without starting the server."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.main import app  # noqa: E402


def main():
    out = Path("docs/openapi.json")
    out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
