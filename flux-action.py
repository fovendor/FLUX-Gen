import asyncio
import re
import base64
import os
from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path

from open_webui.config import CACHE_DIR

IMAGE_CACHE_DIR = Path(CACHE_DIR).joinpath("image/generations/")
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def find_generated_image_path(messages):
    """
    Ищет последнее ассистентское сообщение с ![BFL Image](...),
    возвращает строку вида "/cache/image/generations/xxx.png" или None.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and "![BFL Image](" in msg.get("content", ""):
            match = re.search(r"!\[BFL Image\]\(([^)]+)\)", msg["content"])
            if match:
                return match.group(1)
    return None


class Action:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__=None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> Optional[dict]:
        print(f"action:{__name__} called with body:", body)

        # 1. Ищем последнее сгенерированное ассистентом изображение:
        messages = body.get("messages", [])
        image_path = find_generated_image_path(messages)
        if not image_path:
            error_msg = "Не найдено последнее сгенерированное изображение в сообщениях!"
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": error_msg, "done": True},
                    }
                )
            return {"status": "error", "message": error_msg}

        # 2) Из пути выделяем имя файла
        filename = os.path.basename(image_path)
        full_file_path = IMAGE_CACHE_DIR / filename
        if not full_file_path.exists():
            error_msg = f"Файл не найден: {full_file_path}"
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": error_msg, "done": True}}
                )
            return {"status": "error", "message": error_msg}

        # 3) Формируем HTML (fetch + FileReader, без вставки base64 внутрь чата)

        artifact_html = f"""
```html
<head><meta charset="UTF-8"><title>Inpainting Helper</title><style>body{{font-family:Arial,sans-serif;margin:20px;max-width:1000px;}}canvas{{max-width:100%;height:auto;border:1px solid #ccc;margin:10px 0;}}.input-group{{margin:10px 0;}}textarea{{width:100%;height:150px;margin:10px 0;resize:vertical;}}button{{padding:8px 16px;margin:5px;cursor:pointer;background:#007bff;color:white;border:none;border-radius:4px;}}button:hover{{background:#0056b3;}}.coords-info{{margin:10px 0;color:#666;}}</style></head><body><h1>Inpainting Helper</h1><p>Изображение загружается фоновым fetch'ом (без огромного base64 в чате).</p><div class="coords-info" id="coordsInfo">Не выделено</div><canvas id="imageCanvas"></canvas><div><button id="generateMaskBtn">Сформировать маску</button><button id="resetBtn">Сброс</button></div><div class="input-group"><label for="promptInput">Введите промт для замены:</label><textarea id="promptInput" placeholder="Опишите, что вы хотите видеть в выделенной области..."></textarea></div><div><h2>Base64 исходного изображения</h2><textarea id="originalBase64" readonly></textarea><button id="copyOriginalBtn">Копировать</button></div><div><h2>Base64 маски (PNG)</h2><textarea id="maskBase64" readonly></textarea><button id="copyMaskBtn">Копировать</button></div><script>const canvas=document.getElementById("imageCanvas");const ctx=canvas.getContext("2d");let originalImage=new Image();let isDrawing=false;let startX,startY;let currentRect=null;let overlayOpacity=0.7;function getCanvasCoords(e){{const r=canvas.getBoundingClientRect(),sx=canvas.width/r.width,sy=canvas.height/r.height;return{{x:(e.clientX-r.left)*sx,y:(e.clientY-r.top)*sy}}}}function drawFadedBackground(){{ctx.drawImage(originalImage,0,0);ctx.fillStyle="rgba(255,255,255,"+overlayOpacity+")";ctx.fillRect(0,0,canvas.width,canvas.height)}}function drawSelection(){{if(!currentRect)return;ctx.save();ctx.beginPath();ctx.rect(currentRect.x,currentRect.y,currentRect.w,currentRect.h);ctx.clip();ctx.drawImage(originalImage,0,0);ctx.restore();ctx.strokeStyle="#ff0000";ctx.lineWidth=2;ctx.strokeRect(currentRect.x,currentRect.y,currentRect.w,currentRect.h)}}function redrawCanvas(){{ctx.clearRect(0,0,canvas.width,canvas.height);if(!originalImage)return;drawFadedBackground();if(currentRect)drawSelection()}}canvas.addEventListener("mousedown",e=>{{if(!originalImage)return;const c=getCanvasCoords(e);startX=c.x;startY=c.y;isDrawing=true}});canvas.addEventListener("mousemove",e=>{{if(!isDrawing)return;const c=getCanvasCoords(e);currentRect={{x:Math.min(startX,c.x),y:Math.min(startY,c.y),w:Math.abs(c.x-startX),h:Math.abs(c.y-startY)}};redrawCanvas();updateCoordsInfo()}});canvas.addEventListener("mouseup",()=>isDrawing=false);canvas.addEventListener("mouseleave",()=>isDrawing=false);function updateCoordsInfo(){{const i=currentRect?("X:"+Math.round(currentRect.x)+",Y:"+Math.round(currentRect.y)+",W:"+Math.round(currentRect.w)+",H:"+Math.round(currentRect.h)):"Не выделено";document.getElementById("coordsInfo").textContent=i}}document.getElementById("generateMaskBtn").addEventListener("click",()=>{{if(!currentRect||!originalImage){{alert("Пожалуйста, выделите область на изображении.");return}}const m=document.createElement("canvas");m.width=originalImage.naturalWidth;m.height=originalImage.naturalHeight;const mx=m.getContext("2d");mx.fillStyle="#000000";mx.fillRect(0,0,m.width,m.height);mx.fillStyle="#ffffff";mx.fillRect(currentRect.x,currentRect.y,currentRect.w,currentRect.h);document.getElementById("maskBase64").value=m.toDataURL("image/png")}});document.getElementById("copyOriginalBtn").addEventListener("click",()=>copyToClipboard("originalBase64"));document.getElementById("copyMaskBtn").addEventListener("click",()=>copyToClipboard("maskBase64"));function copyToClipboard(id){{const t=document.getElementById(id);t.select();t.setSelectionRange(0,99999);navigator.clipboard.writeText(t.value).then(()=>alert("Текст скопирован.")).catch(e=>(console.error(e),alert("Не удалось скопировать.")))}}document.getElementById("resetBtn").addEventListener("click",()=>{{ctx.clearRect(0,0,canvas.width,canvas.height);originalImage=null;currentRect=null;document.getElementById("originalBase64").value="";document.getElementById("maskBase64").value="";document.getElementById("coordsInfo").textContent="Не выделено";document.getElementById("promptInput").value="";canvas.width=0;canvas.height=0}});const fileUrl="/cache/image/generations/{filename}";fetch(fileUrl).then(r=>{{if(!r.ok)throw new Error("Ошибка загрузки: "+r.status);return r.blob()}}).then(b=>{{const rd=new FileReader();rd.onload=e=>{{const d=e.target.result;document.getElementById("originalBase64").value=d;originalImage.onload=()=>{{canvas.width=originalImage.naturalWidth;canvas.height=originalImage.naturalHeight;redrawCanvas()}};originalImage.src=d}};rd.readAsDataURL(b)}}).catch(e=>(alert("Fetch error: "+e),console.error(e)));</script></body>
```
"""
        # 4) Возвращаем сообщение-арт
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Rendering artifact with the last generated image (no large base64 in chat)...",
                        "done": False,
                    },
                }
            )
            await asyncio.sleep(1)

            # Это "assistant"-сообщение, содержащее ```html
            await __event_emitter__(
                {
                    "type": "message",
                    "data": {"role": "assistant", "content": artifact_html},
                }
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Artifact rendered successfully",
                        "done": True,
                    },
                }
            )

        return {"status": "ok"}
