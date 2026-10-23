"""Create original CC0 procedural 60-second demo film (not real-world footage).

Pillow is a generation-only dependency. The installed runtime needs only its
offline imageio-ffmpeg wheel, and does not import Pillow.
"""
import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default=str(Path(__file__).resolve().parents[1]/'src'/'verified_alexa'/'assets'))
    parser.add_argument('--ffmpeg',required=True)
    args=parser.parse_args()
    destination=Path(args.output);destination.mkdir(parents=True,exist_ok=True)
    target=destination/'clip-long.webm'
    font_file=Path('C:/Windows/Fonts/arial.ttf')
    font=ImageFont.truetype(str(font_file),25) if font_file.exists() else ImageFont.load_default(size=25)
    small=ImageFont.truetype(str(font_file),16) if font_file.exists() else ImageFont.load_default(size=16)
    process=subprocess.Popen([args.ffmpeg,'-y','-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','640x360',
        '-r','15','-i','pipe:0','-an','-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','4','-crf','24','-b:v','0','-pix_fmt','yuv420p',str(target)],
        stdin=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for frame in range(15*60):
            t=frame/15
            image=Image.new('RGB',(640,360),(12+int(t/6),31+int(t/3),62+int(t/5)))
            draw=ImageDraw.Draw(image)
            sun_x=45+int(t*7.7);sun_y=157-int(75*math.sin(t*math.pi/60))
            draw.ellipse((sun_x-23,sun_y-23,sun_x+23,sun_y+23),fill=(244,191,103))
            draw.polygon([(0,225),(75,143),(158,222),(235,182),(310,225)],fill=(31,68,77))
            draw.polygon([(267,228),(332,179),(418,221),(486,151),(603,222),(640,208),(640,265),(267,265)],fill=(37,75,79))
            draw.rectangle((0,233,640,360),fill=(16,74,96))
            for y in range(244,360,17):
                for x in range(-60,640,90):
                    offset=int(18*math.sin(t*1.3+y/15));draw.arc((x+offset,y,x+offset+70,y+12),0,180,fill=(74,149,157),width=2)
            # An island lighthouse and visible passing sailboat give the movie a
            # recognizable whole scene rather than calibration colour patches.
            draw.polygon([(405,268),(459,239),(523,266)],fill=(41,85,72))
            draw.rectangle((450,160,473,242),fill=(232,225,185));draw.rectangle((449,180,474,192),fill=(184,83,67))
            draw.rectangle((446,151,477,165),fill=(232,194,94));draw.polygon([(444,151),(462,136),(480,151)],fill=(151,68,68))
            boat_x=50+int(t*7)%525;boat_y=278+int(4*math.sin(t))
            draw.polygon([(boat_x-24,boat_y),(boat_x+24,boat_y),(boat_x+15,boat_y+13),(boat_x-15,boat_y+13)],fill=(240,165,90))
            draw.line((boat_x,boat_y,boat_x,boat_y-55),fill=(226,226,216),width=3)
            draw.polygon([(boat_x+3,boat_y-52),(boat_x+3,boat_y-8),(boat_x+31,boat_y-8)],fill=(233,239,225))
            draw.rectangle((0,0,640,69),fill=(11,26,45))
            draw.text((20,10),'TIDAL CLOCK',font=font,fill=(236,245,242))
            phase=('DAWN: THE ISLAND WAKES' if t<20 else 'PASSAGE: FOLLOW THE SAIL' if t<40 else 'ARRIVAL: LIGHT ON THE SHORE')
            draw.text((20,42),phase,font=small,fill=(130,217,202))
            draw.rounded_rectangle((19,304,372,344),radius=8,fill=(11,35,54))
            draw.text((31,315),f'ORIGINAL PROCEDURAL FILM  |  {t:05.1f} / 60 s',font=small,fill=(225,235,235))
            draw.rectangle((0,355,int(640*frame/899),359),fill=(104,220,187))
            process.stdin.write(image.tobytes())
            if frame%150==0:print(json.dumps({'generated_seconds':t,'total_seconds':60}),flush=True)
        process.stdin.close()
        error=process.stderr.read().decode(errors='replace')
        if process.wait()!=0:raise RuntimeError(error)
    finally:
        if process.poll() is None:process.kill();process.wait()
    catalog={'clip-long':{'title':'Tidal Clock — original procedural film','filename':target.name,
        'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'duration':60.0,'width':640,'height':360,
        'license':'CC0-1.0','scope':'original procedural demo film, no real-world footage'}}
    (destination/'media_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'CLIP_LONG_LICENSE.txt').write_text('Tidal Clock: original code-generated geometry, text and animation. CC0-1.0. No third-party footage or music. A procedural demonstration, not photoreal footage.\n',encoding='utf-8')
    print(json.dumps({'path':str(target),'bytes':target.stat().st_size,'catalog':catalog}),flush=True)


if __name__=='__main__':main()
