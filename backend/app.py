import os, io, json, base64
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image
from openai import OpenAI

# === cargar .env del backend ===
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")   # lee backend/.env

app = Flask(__name__)
CORS(app)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError(
        "OPENAI_API_KEY no está seteada. "
        "Editá backend/.env y agregá: OPENAI_API_KEY=sk-xxxxxxxx"
    )

client = OpenAI(api_key=api_key)
def to_data_url(image_bytes: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('utf-8')}"

REPORT_EPP_TOOL = {
    "type": "function",
    "function": {
        "name": "report_epp",
        "description": "Devuelve la evaluación estructurada de EPP en la imagen.",
        "parameters": {
            "type": "object",
            "properties": {
                "casco":   {"type":"object","properties":{"present":{"type":"boolean"},"confidence":{"type":"number","minimum":0,"maximum":1}},"required":["present","confidence"]},
                "chaleco": {"type":"object","properties":{"present":{"type":"boolean"},"confidence":{"type":"number","minimum":0,"maximum":1}},"required":["present","confidence"]},
                "gafas":   {"type":"object","properties":{"present":{"type":"boolean"},"confidence":{"type":"number","minimum":0,"maximum":1}},"required":["present","confidence"]},
                "guantes": {"type":"object","properties":{"present":{"type":"boolean"},"confidence":{"type":"number","minimum":0,"maximum":1}},"required":["present","confidence"]},
                "botas":   {"type":"object","properties":{"present":{"type":"boolean"},"confidence":{"type":"number","minimum":0,"maximum":1}},"required":["present","confidence"]},
                "comentarios":   {"type":"string"},
                "photo_quality": {"type":"string","enum":["ok","low_light","blurry","occluded","too_far"]},
                "required_echo": {"type":"array","items":{"type":"string"}},
                "meets_requirements": {"type":"boolean"},
                "missing_required":  {"type":"array","items":{"type":"string"}}
            },
            "required": ["casco","chaleco","gafas","guantes","botas","meets_requirements","missing_required"]
        }
    }
}

SYSTEM_PROMPT = (
    "Eres un inspector de seguridad industrial. Debes DETECTAR EPP en una foto.\n"
    "SIEMPRE responde llamando a la función report_epp respetando EXACTAMENTE el schema provisto.\n"
    "Criterio de cumplimiento:\n"
    "- Se consideran REQUERIDOS los elementos listados por el usuario.\n"
    "- Un EPP cuenta como presente si present=true Y confidence >= MIN_CONF indicada.\n"
    "- Devuelve 'meets_requirements' y 'missing_required' acorde a ese criterio.\n"
)

@app.post("/analyze")
def analyze():
    file = request.files.get("image")
    if not file:
        return jsonify({"ok": False, "error": "image file missing"}), 400

    # Requisitos desde el front
    try:
        required = json.loads(request.form.get("required", "[]"))
        if not isinstance(required, list): required = []
    except Exception:
        required = []
    try:
        min_conf = float(request.form.get("min_conf", "0.6"))
    except Exception:
        min_conf = 0.6

    raw = file.read()
    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        return jsonify({"ok": False, "error": "invalid image"}), 400

    data_url = to_data_url(raw)

    # Mensaje de usuario con contexto de requisitos
    user_text = (
        "Analiza la imagen y devuelve SOLO la función report_epp.\n"
        f"Required items: {required}\n"
        f"MIN_CONF: {min_conf}\n"
        "Si un requerido no está visible o su 'confidence' es menor a MIN_CONF, debe figurar en 'missing_required'.\n"
        "Incluye 'required_echo' con el eco exacto de la lista de requeridos."
    )

    try:
        chat = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content": SYSTEM_PROMPT},
                {"role":"user","content":[
                    {"type":"text","text": user_text},
                    {"type":"image_url","image_url":{"url": data_url}}
                ]}
            ],
            tools=[REPORT_EPP_TOOL],
            tool_choice={"type":"function","function":{"name":"report_epp"}}
        )

        choice = chat.choices[0]
        tool_calls = getattr(choice.message, "tool_calls", None)
        if not tool_calls:
            return jsonify({"ok": False, "error": "model did not call the tool"}), 500

        call = tool_calls[0]
        if call.function.name != "report_epp":
            return jsonify({"ok": False, "error": f"unexpected tool {call.function.name}"}), 500

        try:
            result = json.loads(call.function.arguments or "{}")
        except Exception as e:
            return jsonify({"ok": False, "error": f"invalid tool args: {e}"}), 500

        # Validaciones mínimas
        for k in ["casco","chaleco","gafas","guantes","botas"]:
            if k not in result:
                return jsonify({"ok": False, "error": f"missing key '{k}'"}), 500
            conf = result[k].get("confidence", None)
            pres = result[k].get("present", None)
            if not isinstance(pres, bool) or not isinstance(conf, (int,float)):
                return jsonify({"ok": False, "error": f"bad type in '{k}'"}), 500

        if "meets_requirements" not in result or "missing_required" not in result:
            return jsonify({"ok": False, "error": "missing compliance fields"}), 500

        return jsonify({"ok": True, "result": result})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
