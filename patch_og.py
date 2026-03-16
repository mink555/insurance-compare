"""Patch Streamlit's index.html with custom OG meta tags.

Dockerfile에서 빌드 시 1회만 실행. 이미 패치된 경우 재패치하지 않는다.
"""
import os
import streamlit
from pathlib import Path

_OG_IMAGE_URL = os.getenv(
    "OG_IMAGE_URL",
    "https://huggingface.co/spaces/minkyyee/insurance-compare/resolve/main/image.png",
)

OG_TAGS = f"""    <meta property="og:title" content="라이나 인사이트 - 보험 상품 분석 솔루션" />
    <meta property="og:description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />
    <meta property="og:image" content="{_OG_IMAGE_URL}" />
    <meta property="og:type" content="website" />
    <meta name="description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />"""

_PATCHED_TITLE = "라이나 인사이트 - 보험 상품 분석 솔루션"

idx = Path(streamlit.__file__).parent / "static" / "index.html"
html = idx.read_text(encoding="utf-8")

if _PATCHED_TITLE not in html:
    html = html.replace(
        "<title>Streamlit</title>",
        f"<title>{_PATCHED_TITLE}</title>\n" + OG_TAGS,
    )
    idx.write_text(html, encoding="utf-8")
    print(f"Patched: {idx}")
else:
    print(f"Already patched: {idx}")
