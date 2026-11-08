#!/bin/bash
# Radar de Garagem — instala no Mac o download automático dos vídeos HD dos carros.
# Uso (cole no Terminal):  bash -c "$(curl -fsSL https://raw.githubusercontent.com/daniloalanm-hash/bruto/main/narrado/mac/instalar.sh)"
set -e
RAW="https://raw.githubusercontent.com/daniloalanm-hash/bruto/main/narrado"
DIR="$HOME/RadarDeGaragem"
ROTULO="br.radardegaragem.baixar"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
mkdir -p "$DIR" "$HOME/Library/LaunchAgents"
cd "$DIR"

echo "1/4 Baixando o script…"
curl -fsSL "$RAW/baixar_carros.py" -o baixar_carros.py

echo "2/4 Preparando o Python (só na primeira vez; pode levar 1–2 min)…"
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  UV="$HOME/.local/bin/uv"
fi
[ -x venv/bin/python ] || "$UV" venv --quiet --python 3.12 venv
"$UV" pip install --quiet --python venv/bin/python --upgrade pip "yt-dlp[default,curl-cffi]" imageio-ffmpeg

echo "3/4 Senha do app Bruto (a mesma da tela; fica guardada só neste Mac):"
venv/bin/python baixar_carros.py --configurar </dev/tty

cat > rodar.sh <<EOS
#!/bin/bash
# roda de hora em hora (launchd): atualiza o script e baixa o que faltar
cd "$DIR"
curl -fsSL "$RAW/baixar_carros.py" -o .novo.py && mv .novo.py baixar_carros.py
echo "===== \$(date '+%d/%m %H:%M')" >> log.txt
venv/bin/python baixar_carros.py >> log.txt 2>&1
tail -n 3000 log.txt > .log && mv .log log.txt
EOS
chmod +x rodar.sh

echo "4/4 Ligando a rodada automática (de hora em hora, em segundo plano)…"
cat > "$PLIST" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/rodar.sh</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
</dict></plist>
EOP
launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Pronto! A partir de agora o Mac baixa sozinho, de hora em hora, os vídeos HD dos carros dos roteiros"
echo "e manda para a biblioteca do app. A primeira rodada já começou em segundo plano."
echo "Acompanhar:  tail -f ~/RadarDeGaragem/log.txt"
echo "Desligar:    launchctl bootout gui/\$(id -u)/$ROTULO"
