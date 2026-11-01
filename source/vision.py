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

def hud_icon_pairs(image):
    """Locate two aligned gold cash/card icons before trusting any OCR numbers."""
    w,h=image.size;left=0;top=int(h*.68);right=int(w*.48)
    region=image.convert('RGB').crop((left,top,right,h));rw,rh=region.size
    mask=bytearray(1 if r>160 and g>110 and b<115 and r>=g*.92 and g>b*1.5 else 0 for r,g,b in region.getdata())
    components=[]
    for offset in range(len(mask)):
        if not mask[offset]:continue
        mask[offset]=0;stack=[offset];xmin=xmax=offset%rw;ymin=ymax=offset//rw;count=0
        while stack:
            at=stack.pop();x=at%rw;y=at//rw;count+=1;xmin=min(xmin,x);xmax=max(xmax,x);ymin=min(ymin,y);ymax=max(ymax,y)
            for neighbor in (at-1 if x else -1,at+1 if x+1<rw else -1,at-rw if y else -1,at+rw if y+1<rh else -1):
                if neighbor>=0 and mask[neighbor]:mask[neighbor]=0;stack.append(neighbor)
        cw=xmax-xmin+1;ch=ymax-ymin+1
        # Rectangular money icons, excluding dollar glyphs, text and large buttons.
        if .006*w<=cw<=.018*w and .006*h<=ch<=.024*h and 1.15<=cw/ch<=2.8 and count>=cw*ch*.30:
            components.append((xmin,top+ymin,xmax+1,top+ymax+1,count))
    pairs=[]
    for a in components:
        for b in components:
            ah=a[3]-a[1];bh=b[3]-b[1];aw=a[2]-a[0];bw=b[2]-b[0];gap=b[1]-a[1]
            if abs(a[0]-b[0])<=max(2,w*.0025) and .75<=aw/bw<=1.33 and .7<=ah/bh<=1.45 and max(ah,bh)*1.05<=gap<=max(ah,bh)*2.8:
                pairs.append((a,b))
    return sorted(pairs,key=lambda ab:ab[1][3],reverse=True)

def read_image(image):
    w,h=image.size;anchors=hud_icon_pairs(image)
    if not anchors:return None,[]
    # Ambiguous discovery is rejected rather than guessing between unrelated icons.
    if len(anchors)!=1:return None,[]
    cash,bank=anchors[0];top=int(h*.68)
    # Keep the OCR context that distinguishes the HUD font's 3/5 glyphs, then
    # accept only text physically beside the two validated icons.
    crop=image.crop((0,top,int(w*.48),h))
    scale=min(3,2800/crop.width)
    crop=crop.resize((round(crop.width*scale),round(crop.height*scale)))
    def anchored(lines):
        chosen=[]
        for icon in (cash,bank):
            cx,cy,cr,cb,_=icon;ih=cb-cy
            matches=[line for line in lines if cx-4<=line[1]/scale<=cr+w*.022 and abs(top+(line[2]+line[3]/2)/scale-(cy+cb)/2)<=ih*.9]
            if not matches:return []
            chosen.append(min(matches,key=lambda line:abs(top+(line[2]+line[3]/2)/scale-(cy+cb)/2)))
        return chosen
    lines=asyncio.run(recognize(crop))
    pair=parse_lines(anchored(lines))
    if pair is None:
        # White digits survive this mask while background scenery is suppressed.
        white=crop.convert('RGB').point(lambda x: 255 if x>205 else 0).convert('L')
        lines2=asyncio.run(recognize(white))
        pair=parse_lines(anchored(lines2))
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
