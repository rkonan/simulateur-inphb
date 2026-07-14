from datetime import datetime, timezone

import requests


WEB_APP_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbyCTFYlfGUfS0Icc6bc3MqRfa4ZoRE7GR2maQFV08Ve3VVtfYItH1IkuM7jMIKzu1yCLw"
    "/exec"
)


def main() -> None:
    payload = {
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "message": "Test depuis Python",
    }

    try:
        response = requests.post(
            WEB_APP_URL,
            json=payload,
            timeout=30,
            allow_redirects=True,
        )

        print("Code HTTP :", response.status_code)
        print("Réponse :", response.text)

        response.raise_for_status()

    except requests.RequestException as exc:
        print("Erreur :", exc)


if __name__ == "__main__":
    main()