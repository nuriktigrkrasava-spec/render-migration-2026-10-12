#!/bin/bash
# Конвертирует sales_demo_raw.webm → sales_demo.mp4 с продающими титрами.
set -e

cd "$(dirname "$0")/.."
FFMPEG=$(.venv/bin/python3 -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
FONT="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SRC="docs/video/sales_demo_raw.webm"
OUT="docs/video/sales_demo.mp4"

# Каждый drawtext: текст, тайминги (enable=between(t,from,to)), цвет, размер.
# fade-in 0.5с / fade-out 0.5с через alpha-expression.
FADE="if(lt(t,${T_FROM}+0.3),(t-${T_FROM})/0.3,if(lt(t,${T_TO}-0.3),1,(${T_TO}-t)/0.3))"

"$FFMPEG" -y -i "$SRC" -vf "\
drawtext=fontfile='$FONT':text='iiko Analytics':fontcolor=white:fontsize=46:\
box=1:boxcolor=black@0.6:boxborderw=14:\
x=(w-text_w)/2:y=120:\
enable='between(t,0.5,4)':\
alpha='if(lt(t,1),(t-0.5)*2,if(lt(t,3.5),1,(4-t)*2))',\
\
drawtext=fontfile='$FONT':text='Аналитика ресторана в Telegram':fontcolor=white:fontsize=22:\
box=1:boxcolor=black@0.6:boxborderw=10:\
x=(w-text_w)/2:y=200:\
enable='between(t,0.7,4)':\
alpha='if(lt(t,1.2),(t-0.7)*2,if(lt(t,3.5),1,(4-t)*2))',\
\
drawtext=fontfile='$FONT':text='Все метрики ресторана':fontcolor=white:fontsize=28:\
box=1:boxcolor=black@0.65:boxborderw=12:\
x=(w-text_w)/2:y=h-200:\
enable='between(t,4.5,7.5)':\
alpha='if(lt(t,5),(t-4.5)*2,if(lt(t,7),1,(7.5-t)*2))',\
\
drawtext=fontfile='$FONT':text='Все каналы продаж':fontcolor=white:fontsize=28:\
box=1:boxcolor=black@0.65:boxborderw=12:\
x=(w-text_w)/2:y=h-200:\
enable='between(t,8,11)':\
alpha='if(lt(t,8.5),(t-8)*2,if(lt(t,10.5),1,(11-t)*2))',\
\
drawtext=fontfile='$FONT':text='Анализ цен поставщиков':fontcolor=white:fontsize=30:\
box=1:boxcolor=black@0.7:boxborderw=14:\
x=(w-text_w)/2:y=140:\
enable='between(t,12,15)':\
alpha='if(lt(t,12.5),(t-12)*2,if(lt(t,14.5),1,(15-t)*2))',\
\
drawtext=fontfile='$FONT':text='Найди завышенные цены':fontcolor=yellow:fontsize=28:\
box=1:boxcolor=black@0.75:boxborderw=14:\
x=(w-text_w)/2:y=140:\
enable='between(t,16,21)':\
alpha='if(lt(t,16.5),(t-16)*2,if(lt(t,20.5),1,(21-t)*2))',\
\
drawtext=fontfile='$FONT':text='Подключи свой iiko':fontcolor=white:fontsize=34:\
box=1:boxcolor=black@0.75:boxborderw=16:\
x=(w-text_w)/2:y=(h-text_h)/2:\
enable='between(t,21.2,22.4)':\
alpha='if(lt(t,21.7),(t-21.2)*2,1)'\
" \
-c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -movflags +faststart \
-an "$OUT"

echo ""
echo "✓ $OUT"
ls -lh "$OUT"
