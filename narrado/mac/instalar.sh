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
# atualiza o script e baixa os vídeos HD dos carros que faltam na biblioteca
cd "$DIR"
curl -fsSL "$RAW/baixar_carros.py" -o .novo.py && mv .novo.py baixar_carros.py
venv/bin/python baixar_carros.py "\$@" 2>&1 | tee -a log.txt
tail -n 3000 log.txt > .log && mv .log log.txt
EOS
chmod +x rodar.sh

# versão antiga rodava sozinha de hora em hora: desliga
launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
rm -f "$PLIST"

echo "4/4 Criando o atalho na Mesa…"
ATALHO="$HOME/Desktop/Baixar carros HD.command"
cat > "$ATALHO" <<EOA
#!/bin/bash
"$DIR/rodar.sh"
echo
read -n 1 -s -p "Terminou. Aperte qualquer tecla para fechar."
EOA
chmod +x "$ATALHO"

echo
echo "Pronto! Antes de gerar um vídeo de carros, dê dois cliques em \"Baixar carros HD\" na Mesa."
echo "Ele baixa em HD só os carros dos roteiros que ainda não estão na biblioteca e manda para o app."
echo "Não roda sozinho e não usa IA (não gasta token)."
