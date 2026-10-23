"""Offline original procedural media generation; Pillow + local FFmpeg only."""
import json
import math
import subprocess
import sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

dest=Path(sys.argv[1]); dest.mkdir(parents=True,exist_ok=True)
ffmpeg=sys.argv[2]
catalog={
 'clip-a':('CLIP A  BLUE ORBIT',[(32,224,96),(32,96,224),(224,64,32),(224,224,32),(192,64,224)]),
 'clip-b':('CLIP B  AMBER TRAIL',[(224,32,64),(224,192,64),(32,224,224),(224,64,192),(64,224,32)]),
}
font_path=Path('C:/Windows/Fonts/arial.ttf')
font=ImageFont.truetype(str(font_path),30) if font_path.exists() else ImageFont.load_default(size=30)
small=ImageFont.truetype(str(font_path),20) if font_path.exists() else ImageFont.load_default(size=20)
for key,(title,colors) in catalog.items():
 path=dest/(key+'.webm')
 args=[ffmpeg,'-y','-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','640x360','-r','24','-i','pipe:0','-an','-c:v','libvpx-vp9','-crf','18','-b:v','0','-pix_fmt','yuv420p',str(path)]
 proc=subprocess.Popen(args,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
 for frame in range(24*12):
  t=frame/24
  im=Image.new('RGB',(640,360),(10,35,67) if key=='clip-a' else (62,22,35))
  draw=ImageDraw.Draw(im)
  for i,color in enumerate(colors):draw.rectangle((16+i*64,16,48+i*64,48),fill=color)
  draw.text((24,76),title,font=font,fill=(240,248,255))
  draw.text((24,119),'ORIGINAL CC0 TEST SCENE  |  '+key.upper(),font=small,fill=(170,190,210))
  draw.text((24,158),f'ACTUAL VIDEO TIME: {t:04.1f} s',font=small,fill=(240,240,240))
  for x in range(0,640,40):draw.line((x,208,x,359),fill=(32,62,90) if key=='clip-a' else (80,40,50),width=1)
  for y in range(208,360,30):draw.line((0,y,640,y),fill=(32,62,90) if key=='clip-a' else (80,40,50),width=1)
  x=70+int((math.sin(t*.65)+1)*250)
  y=270+int(math.sin(t*1.35)*42)
  if key=='clip-a':
   draw.ellipse((x-24,y-24,x+24,y+24),fill=(55,216,238),outline=(230,255,255),width=3)
   draw.ellipse((x-45,y-8,x+45,y+8),outline=(190,240,255),width=3)
  else:
   draw.polygon([(x,y-30),(x+30,y),(x,y+30),(x-30,y)],fill=(255,190,45),outline=(255,245,180))
   draw.line((x-70,y+35,x-35,y+15),fill=(225,95,60),width=8)
  draw.rectangle((0,344,int(640*frame/(24*12-1)),359),fill=(40,190,200) if key=='clip-a' else (210,125,30))
  proc.stdin.write(im.tobytes())
 proc.stdin.close()
 error=proc.stderr.read().decode(errors='replace')
 if proc.wait()!=0:raise RuntimeError(error)
 print(json.dumps({'clip':key,'path':str(path),'bytes':path.stat().st_size}))
