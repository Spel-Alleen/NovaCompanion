import asyncio
import io
import ctypes
import re
from pathlib import Path
from PIL import Image, ImageOps, ImageEnhance
from core import parse_lines

async def recognize(image):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    engine=OcrEngine.try_create_from_language(Language('en-US')) or OcrEngine.try_create_from_user_profile_languages()
    if engine is None: raise RuntimeError('В Windows не установлен язык распознавания. Добавь английский языковой пакет.')
    out=io.BytesIO(); image.save(out,format='PNG')
    stream=InMemoryRandomAccessStream(); writer=DataWriter(stream)
    writer.write_bytes(out.getvalue()); await writer.store_async(); await writer.flush_async(); writer.detach_stream(); stream.seek(0)
    decoder=await BitmapDecoder.create_async(stream)
    bitmap=await decoder.get_software_bitmap_async()
    result=await engine.recognize_async(bitmap)
    lines=[]
    for line in result.lines:
        words=list(line.words)
        if not words: continue
        x=min(w.bounding_rect.x for w in words); y=min(w.bounding_rect.y for w in words)
        h=max(w.bounding_rect.height for w in words)
        lines.append((line.text,x,y,h))
    bitmap.close(); stream.close()
    return lines

def read_image(image):
    # HUD starts near the map; relative bounds cover shifted HUDs and different resolutions.
    w,h=image.size
    crop=image.crop((0,int(h*.68),int(w*.48),h))
    scale=min(3,2800/crop.width)
    crop=crop.resize((round(crop.width*scale),round(crop.height*scale)))
    lines=asyncio.run(recognize(crop))
    pair=parse_lines(lines)
    if pair is None:
        # White digits survive this mask while background scenery is suppressed.
        white=crop.convert('RGB').point(lambda x: 255 if x>205 else 0).convert('L')
        lines2=asyncio.run(recognize(white))
        pair=parse_lines(lines2)
        lines+=lines2
    return pair,lines

def game_rect():
    from game_window import game_rect as detect
    return detect()

def capture_game():
    import mss
    rect=game_rect()
    if rect is None: return None
    with mss.mss() as cap:
        shot=cap.grab(rect)
        return Image.frombytes('RGB',shot.size,shot.rgb)

if __name__=='__main__':
    import sys
    pair,lines=read_image(Image.open(sys.argv[1]).convert('RGB'))
    print('BALANCE:',pair)
    print(lines)
