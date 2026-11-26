"""backend/ocr.py — read a match screenshot through an OCR provider.

Three providers, all reachable without new tooling beyond the backend's requirements:
* Google Cloud Vision — DOCUMENT_TEXT_DETECTION, keyed by `GOOGLE_VISION_API_KEY`.
* Azure AI Document Intelligence — prebuilt-layout (text plus table extraction), keyed by
  `AZURE_DOC_ENDPOINT` + `AZURE_DOC_KEY`.
* Tesseract — no key at all, runs on the server. Needs the `tesseract-ocr` program (apt) plus the `pytesseract`
  and `Pillow` packages. It is the fallback, so a screenshot can be read with nothing configured.

The keys stay server-side: the browser uploads the image to this API, which calls the provider. Nothing is stored —
the text comes back to the manager for review before the match is imported, and the scan parser tidies it into
score/stat rows.
"""

import asyncio
import base64
import io

import httpx
from fastapi import HTTPException

from shared import settings as env

MAX_BYTES = 6 * 1024 * 1024
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
AZURE_API_VERSION = "2024-11-30"
AZURE_POLLS = 25
PROVIDERS = {"google": "Google Cloud Vision", "azure": "Azure AI Document Intelligence", "local": "Tesseract (no key)"}
TESSERACT_HINT = "The backend needs Tesseract for keyless reading: install `tesseract-ocr` and the `pytesseract` + `Pillow` packages."


def _local_ready() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def configured() -> dict:
    return {"google": bool(env.GOOGLE_VISION_API_KEY),
            "azure": bool(env.AZURE_DOC_ENDPOINT and env.AZURE_DOC_KEY),
            "local": _local_ready()}


def pick(requested: str | None = None) -> str:
    """Honour the request, then OCR_PROVIDER, then the best available: cloud key first, Tesseract last."""
    conf = configured()
    want = (requested or env.OCR_PROVIDER or "").strip().lower()
    if want in PROVIDERS:
        if not conf[want]:
            raise HTTPException(422, {"errors": {"image": f"{PROVIDERS[want]} isn't available on the backend — {TESSERACT_HINT}"}})
        return want
    for name in ("google", "azure", "local"):
        if conf[name]:
            return name
    raise HTTPException(422, {"errors": {"image": "Screenshot reading isn't set up — add GOOGLE_VISION_API_KEY or the Azure "
                                                  "Document Intelligence keys, or install Tesseract (`tesseract-ocr` + "
                                                  "`pytesseract` + `Pillow`) for keyless reading."}})


async def extract_text(data: bytes, mime: str = "image/png", provider: str | None = None) -> tuple[str, str]:
    """Image bytes -> (text, provider name). Raises 422/502 with a message that is safe to show the manager."""
    if not data:
        raise HTTPException(422, {"errors": {"image": "Add a screenshot first."}})
    if len(data) > MAX_BYTES:
        raise HTTPException(422, {"errors": {"image": "That screenshot is too big (max 6 MB)."}})
    name = pick(provider)
    if name == "google":
        text = await _google(data)
    elif name == "azure":
        text = await _azure(data, mime)
    else:
        text = await _local(data)
    text = (text or "").strip()
    if not text:
        raise HTTPException(422, {"errors": {"image": "The OCR service didn't find any text in that screenshot."}})
    return text, name


async def _google(data: bytes) -> str:
    body = {"requests": [{"image": {"content": base64.b64encode(data).decode()},
                          "features": [{"type": "DOCUMENT_TEXT_DETECTION"}]}]}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post("https://vision.googleapis.com/v1/images:annotate", params={"key": env.GOOGLE_VISION_API_KEY}, json=body)
    if r.status_code != 200:
        raise HTTPException(502, "Google Vision couldn't read that image — check that the API key is valid and the Cloud Vision API is enabled.")
    res = (r.json().get("responses") or [{}])[0]
    if res.get("error"):
        raise HTTPException(502, f"Google Vision: {str(res['error'].get('message') or 'request failed')[:200]}")
    return (res.get("fullTextAnnotation") or {}).get("text") or ""


async def _azure(data: bytes, mime: str) -> str:
    url = f"{env.AZURE_DOC_ENDPOINT}/documentintelligence/documentModels/prebuilt-layout:analyze"
    headers = {"Ocp-Apim-Subscription-Key": env.AZURE_DOC_KEY, "Content-Type": mime or "image/png"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(url, params={"api-version": AZURE_API_VERSION}, headers=headers, content=data)
        if r.status_code not in (200, 202):
            raise HTTPException(502, "Azure Document Intelligence couldn't read that image — check the endpoint and key.")
        if r.status_code == 200:  # a small image can finish immediately
            return (r.json().get("analyzeResult") or {}).get("content") or ""
        op = r.headers.get("operation-location")
        if not op:
            raise HTTPException(502, "Azure Document Intelligence didn't return a result location.")
        for _ in range(AZURE_POLLS):
            await asyncio.sleep(1)
            p = await c.get(op, headers={"Ocp-Apim-Subscription-Key": env.AZURE_DOC_KEY})
            if p.status_code != 200:
                raise HTTPException(502, "Azure Document Intelligence stopped answering — try again in a moment.")
            body = p.json()
            if body.get("status") == "succeeded":
                return (body.get("analyzeResult") or {}).get("content") or ""
            if body.get("status") == "failed":
                raise HTTPException(502, "Azure Document Intelligence couldn't process that image.")
    raise HTTPException(504, "Azure Document Intelligence took too long — try a smaller or sharper screenshot.")


def _prepare(data: bytes):
    """Make a game screenshot easier for Tesseract: grayscale, contrast, dark theme -> light, and a higher resolution."""
    from PIL import Image, ImageOps, ImageStat
    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    if ImageStat.Stat(img).mean[0] < 110:   # light text on a dark screenshot
        img = ImageOps.invert(img)
    if img.width < 1400:                               # Tesseract reads better around 300 DPI
        scale = min(3, max(2, 1400 // max(img.width, 1)))
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    return img


async def _local(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
    except ImportError:
        raise HTTPException(422, {"errors": {"image": TESSERACT_HINT}})
    try:
        img = await asyncio.to_thread(_prepare, data)
    except Exception:
        raise HTTPException(422, {"errors": {"image": "That image couldn't be opened — try a PNG or JPEG screenshot."}})
    try:
        return await asyncio.to_thread(pytesseract.image_to_string, img, config="--oem 3 --psm 6")
    except pytesseract.TesseractNotFoundError:
        raise HTTPException(422, {"errors": {"image": TESSERACT_HINT}})
    except Exception:
        raise HTTPException(502, "Tesseract couldn't read that image — try a sharper screenshot.")
