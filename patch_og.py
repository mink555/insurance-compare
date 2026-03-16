"""Patch Streamlit's index.html with custom OG meta tags."""
import streamlit
from pathlib import Path

OG_TAGS = """    <meta property="og:title" content="라이나 인사이트 - 보험 상품 분석 솔루션" />
    <meta property="og:description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />
    <meta property="og:image" content="https://huggingface.co/spaces/minkyyee/insurance-compare/resolve/main/image.png" />
    <meta property="og:type" content="website" />
    <meta name="description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />"""

idx = Path(streamlit.__file__).parent / "static" / "index.html"
html = idx.read_text(encoding="utf-8")
html = html.replace(
    "<title>Streamlit</title>",
    "<title>라이나 인사이트 - 보험 상품 분석 솔루션</title>\n" + OG_TAGS,
)
idx.write_text(html, encoding="utf-8")
print(f"Patched: {idx}")
