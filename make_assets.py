import os
from PIL import Image, ImageDraw, ImageFont

os.makedirs('assets', exist_ok=True)
FONT_B = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_R = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

S = 512
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=110, fill=(16, 64, 96, 255))
d.ellipse([116, 96, 396, 376], fill=(255, 255, 255, 255))
d.polygon([(156, 336), (256, 470), (356, 336)], fill=(255, 255, 255, 255))
d.rectangle([216, 156, 296, 316], fill=(24, 168, 96, 255))
d.rectangle([176, 196, 336, 276], fill=(24, 168, 96, 255))
img.save('assets/icon.png')

W, H = 480, 800
sp = Image.new('RGBA', (W, H), (13, 17, 23, 255))
d = ImageDraw.Draw(sp)
ic = img.resize((180, 180))
sp.paste(ic, ((W - 180) // 2, 170), ic)
f_title = ImageFont.truetype(FONT_B, 40)
f_load = ImageFont.truetype(FONT_R, 34)
# заголовок в ДВЕ строки: перенос после двоеточия
t1a = 'Рядом:'
t1b = 'аптеки и банкоматы'
d.text(((W - d.textlength(t1a, font=f_title)) / 2, 390), t1a,
       font=f_title, fill=(240, 244, 250, 255))
d.text(((W - d.textlength(t1b, font=f_title)) / 2, 444), t1b,
       font=f_title, fill=(240, 244, 250, 255))
t2 = 'Загрузка…'
d.text(((W - d.textlength(t2, font=f_load)) / 2, 540), t2,
       font=f_load, fill=(120, 170, 255, 255))
sp.save('assets/presplash.png')
print('assets generated: icon.png, presplash.png')
