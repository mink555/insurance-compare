FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN STATIC_INDEX=$(python -c "import streamlit, pathlib; print(pathlib.Path(streamlit.__file__).parent / 'static' / 'index.html')") && \
    sed -i 's|<title>Streamlit</title>|<title>라이나 인사이트 - 보험 상품 분석 솔루션</title>\n    <meta property="og:title" content="라이나 인사이트 - 보험 상품 분석 솔루션" />\n    <meta property="og:description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />\n    <meta property="og:image" content="https://huggingface.co/spaces/minkyyee/insurance-compare/resolve/main/image.png" />\n    <meta property="og:type" content="website" />\n    <meta name="description" content="라이나생명 암보험 특약 비교 분석 플랫폼" />|' "$STATIC_INDEX"

COPY . .

EXPOSE 7860

CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
