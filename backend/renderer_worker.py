"""Local bounded drawing interpreter and FFmpeg encoder; never executes generated Python."""
import json
import math
import subprocess
import sys
import resource
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).parent))
from drawing_script import DrawingScript

WIDTH, HEIGHT, FPS = 960, 540, 15

class Canvas:
    def __init__(self, image):
        self.draw = ImageDraw.Draw(image)
    def text(self, x, y, text, size=24, color='#ffffff'):
        size = max(8, min(80, int(size)))
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)
        self.draw.multiline_text((x,y), str(text)[:1200], font=font, fill=color, spacing=6)
    def rect(self, x,y,w,h,color='#365c37'):
        self.draw.rounded_rectangle((x,y,x+w,y+h), radius=6, fill=color)
    def circle(self,x,y,r,color='#aacb88'):
        self.draw.ellipse((x-r,y-r,x+r,y+r),fill=color)
    def line(self,x1,y1,x2,y2,color='#aacb88',width=3):
        self.draw.line((x1,y1,x2,y2),fill=color,width=max(1,min(20,int(width))))
    def arrow(self,x1,y1,x2,y2,color='#aacb88'):
        self.line(x1,y1,x2,y2,color)
        a=math.atan2(y2-y1,x2-x1)
        self.draw.polygon([(x2,y2),(x2-12*math.cos(a-.4),y2-12*math.sin(a-.4)),
            (x2-12*math.cos(a+.4),y2-12*math.sin(a+.4))],fill=color)
    def plot(self,points,color='#aacb88'):
        self.draw.line([tuple(p) for p in points],fill=color,width=3)
    @staticmethod
    def lerp(a,b,u): return a+(b-a)*max(0,min(1,u))
    @staticmethod
    def ease(u):
        u=max(0,min(1,u)); return u*u*(3-2*u)


def main(folder=Path('/job')):
    spec=json.loads((folder/'script.json').read_text())
    duration=max(2,min(30,float(spec['duration'])))
    program=DrawingScript(spec['code'])
    function=program.draw
    # Check representative frames before starting the encoder.
    for t in (0,duration/2,max(0,duration-1/FPS)):
        function(Canvas(Image.new('RGB',(WIDTH,HEIGHT),'#172526')),t)
    temporary=folder/'rendering.mp4'
    command=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24',
        '-s',f'{WIDTH}x{HEIGHT}','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264',
        '-preset','ultrafast','-crf','23','-threads','2','-pix_fmt','yuv420p',
        '-movflags','+faststart','-fs',str(20*1024*1024),str(temporary)]
    with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as encoder:
        try:
            for frame in range(math.ceil(duration*FPS)):
                image=Image.new('RGB',(WIDTH,HEIGHT),'#172526')
                function(Canvas(image),frame/FPS)
                encoder.stdin.write(image.tobytes())
            encoder.stdin.close()
            if encoder.wait()!=0: raise RuntimeError('Encoding failed.')
        except BaseException:
            encoder.kill(); encoder.wait(); raise
    temporary.replace(folder/'video.mp4')

if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_CPU,(45,45))
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE,(20*1024*1024,20*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
    main(Path(sys.argv[1]))
