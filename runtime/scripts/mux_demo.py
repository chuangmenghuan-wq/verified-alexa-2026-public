"""Mux actual captured renderer with disclosed synthesized narration and subtitles."""
import argparse,json,subprocess,wave
from pathlib import Path
import imageio_ffmpeg
parser=argparse.ArgumentParser()
parser.add_argument('--frames',required=True)
parser.add_argument('--narration',required=True)
parser.add_argument('--audio',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args()
frames=Path(args.frames).resolve(); config=json.loads(Path(args.narration).read_text(encoding='utf-8-sig'))
audio=Path(args.audio).resolve(); output=Path(args.output).resolve(); output.parent.mkdir(parents=True,exist_ok=True)
rate=22050; length=float(config.get('duration_seconds',120)); buffer=bytearray(round(rate*2*length)); subtitles=[]; measured=[]
assert 90<=length<=120, 'Competition walkthrough must be 90–120 seconds'
def timestamp(seconds):
    milliseconds=round(seconds*1000); h,milliseconds=divmod(milliseconds,3600000); m,milliseconds=divmod(milliseconds,60000); s,ms=divmod(milliseconds,1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'
for index,segment in enumerate(config['segments']):
    with wave.open(str(audio/(segment['id']+'.wav'))) as wav:
        assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,rate)
        raw=wav.readframes(wav.getnframes())
    start=segment['at_seconds']; end=start+len(raw)/(rate*2)
    assert end<=length, 'Narration extends beyond actual recording'
    next_start=config['segments'][index+1]['at_seconds'] if index+1<len(config['segments']) else length
    assert end<=next_start, 'Narration segments overlap'
    offset=round(start*rate)*2; buffer[offset:offset+len(raw)]=raw
    words=segment['text'].split(); rows=[' '.join(words[i:i+12]) for i in range(0,len(words),12)]
    # Split long captions in real narration-time order rather than covering the screen.
    for row_index,row in enumerate(rows):
        begin=start+(end-start)*row_index/len(rows); finish=start+(end-start)*(row_index+1)/len(rows)
        subtitles += [str(len(subtitles)//4+1),timestamp(begin)+' --> '+timestamp(finish),row,'']
    measured.append({**segment,'duration_seconds':end-start})
wavfile=output.with_suffix('.wav')
with wave.open(str(wavfile),'wb') as wav:
    wav.setparams((1,2,rate,0,'NONE','not compressed')); wav.writeframes(buffer)
srt=output.with_suffix('.srt'); srt.write_text('\n'.join(subtitles),encoding='utf-8')
ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
# Run from the output folder so subtitle paths need no Windows colon escaping.
cmd=[ffmpeg,'-y','-f','concat','-safe','0','-i',str(frames/'frames.ffconcat'),'-i',str(wavfile),
     '-vf',"pad=ceil(iw/2)*2:ceil(ih/2)*2+130:0:0:black,subtitles="+srt.name+":force_style='FontName=Arial,FontSize=10,Outline=1,MarginV=8'",
     '-r','24','-c:v','libx264','-preset','fast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-t',str(length),'-movflags','+faststart',output.name]
process=subprocess.run(cmd,cwd=output.parent,capture_output=True)
output.with_suffix('.ffmpeg.log').write_bytes(process.stderr)
if process.returncode:raise RuntimeError('FFmpeg failed; inspect saved log')
output.with_suffix('.audio.json').write_text(json.dumps({'source':'disclosed synthesized English narration',
    'segments':measured,'voice':json.loads((audio/'VOICE_PROVENANCE.json').read_text(encoding='utf-8-sig')),
    'samples':len(buffer)//2,'duration_seconds':length,'scope':'narration audio encoded in MP4; not physical mic or speech service quality'},indent=2),encoding='utf-8')
print(json.dumps({'output':str(output),'duration_seconds':length,'audio_samples':len(buffer)//2}))
