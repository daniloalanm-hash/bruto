# Bruto

Plataforma para tratar vídeo bruto: decupagem automática, montagem assistida pelo Claude, ajuste fino em timeline e exportação. Tudo roda no navegador de quem edita. Os vídeos nunca são enviados a servidor algum, então o Vercel só hospeda a interface (custo zero).

## Publicar no Vercel (grátis)

1. Crie um repositório no GitHub e suba o conteúdo desta pasta.
2. No Vercel: **Add New → Project**, importe o repositório.
3. Framework Preset: **Other**. Sem build command, sem output directory. Clique em Deploy.
4. Compartilhe a URL com o editor.

Teste local: `npx serve .` dentro da pasta (abrir o `index.html` direto pelo explorador de arquivos não funciona, por causa dos web workers).

## Fluxo de trabalho

1. **Adicionar vídeos** (ou arrastar para a janela). Cada arquivo é analisado: o Bruto detecta cortes de cena, gera miniaturas e marca alertas (escuro, estourado, desfocado, muito movimento, curta).
2. **Montar com o Claude**: escolha o tipo de vídeo, descreva o objetivo, baixe as folhas de contato e copie o prompt. Envie tudo numa conversa com o Claude e cole o JSON da resposta em "Aplicar edição".
   Sem o Claude: use **Montar rascunho** (todas as cenas sem alerta) ou monte na mão.
3. **Ajustar**: arraste clipes para reordenar, mude entrada/saída, velocidade e áudio no painel da direita. Modo **Sequência** mostra a prévia do vídeo final.
4. **Exportar**: MP4 renderizado no navegador, script de render local (.bat/.sh, muito mais rápido para vídeos longos ou 4K) ou EDL para DaVinci Resolve/Premiere.

O projeto é salvo automaticamente no navegador. Para passar para outra pessoa ou outro computador, use **Projeto → Salvar projeto (.json)**; quem abrir precisa adicionar os mesmos arquivos de vídeo, que são reconectados pelo nome e tamanho.

## Atalhos

Espaço reproduz/pausa · I marca entrada · O marca saída · A adiciona o trecho · S divide o clipe · Del remove · ← → quadro a quadro (Shift: 1 s) · Ctrl+Z / Ctrl+Shift+Z

## Limitações conhecidas

- Vídeos HEVC/H.265 (padrão do iPhone) podem não abrir no Chrome/Edge do Windows. No iPhone, use Ajustes → Câmera → Formatos → "Mais compatível", ou converta para H.264.
- O render no navegador usa ffmpeg em WebAssembly (single-thread): conte alguns minutos por minuto de vídeo em 1080p. Para 4K, use o script local.
- A EDL não carrega mudanças de velocidade (ficam como comentário em cada evento).
- O motor de render (~32 MB) é baixado do jsDelivr na primeira exportação e fica em cache.

## Formato de edição (o que o Claude devolve)

```json
{
  "format": "bruto-edit",
  "version": 1,
  "settings": { "aspect": "16:9", "fit": "contain" },
  "clips": [
    { "scene": "C004", "in": 12.4, "out": 16.0, "label": "Fachada", "speed": 1, "mute": true, "note": "motivo" }
  ]
}
```

`in`/`out` são segundos dentro do arquivo da cena. Também é aceito `"file": "nome.mp4"` no lugar de `scene`.

## Estrutura

- `index.html`, `styles.css`, `app.js`: a aplicação inteira, sem build.
- `vendor/ffmpeg/`: wrapper do ffmpeg.wasm (@ffmpeg/ffmpeg 0.12.15, MIT). O núcleo (@ffmpeg/core 0.12.10) vem do jsDelivr.
