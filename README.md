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
2. **Montar vídeo**: escolha o tipo, a duração e o formato.
   - **Montar automaticamente**: sem IA e sem configuração. Descarta trechos escuros, desfocados ou tremidos e pega o melhor pedaço de cada cena até completar a duração.
   - **Montar com IA**: usa o Gemini (plano gratuito) para olhar as imagens, identificar os ambientes e decidir a ordem. Precisa de uma chave grátis de aistudio.google.com/apikey, salva só no navegador de quem usa.
   - **Fluxo manual com o Claude**: folhas de contato + prompt, e o JSON da resposta é colado de volta.
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

## Modo Narrado (`/narrado`)

Vídeo vertical "narração sem rosto" a partir de uma notícia: roteiro com IA, sua voz (ou voz de IA), cenas oficiais dos jogos que acompanham o assunto falado, transição a cada troca de tópico e legenda dinâmica sincronizada.

1. **Pauta**: cole o link (sites WordPress, como o blog da Loja dos Gifts, são lidos direto pelo navegador) ou cole o texto.
2. **Roteiro**: o Gemini (mesma chave gratuita do editor) escreve os tópicos; em cada um você escolhe o **jogo na tela**. Imagem de um jogo só aparece quando ele está sendo falado.
3. **Voz**: grave no próprio navegador, envie um áudio ou use a voz de IA. Errou? Pause e repita a frase: fica a última versão.
4. **Vídeo**: o render roda no GitHub Actions (grátis). Depois dá para cortar/trazer tomadas, corrigir a legenda (duplo clique), trocar as imagens de cada tópico e escolher a redução de ruído — e gerar a nova versão.

O que o render faz sozinho: busca screenshots e trailers oficiais na Steam, descarta trechos com logo, tela escura ou texto, transcreve por trechos (Whisper), mantém a última tomada de cada frase, encurta pausas, **equaliza a voz para ficar encorpada** e aplica **redução de ruído só quando o fundo pede**, monta cortes de ~1,7 s com zoom, transição no início de cada tópico e legenda em blocos com destaques em amarelo.

### Configuração (uma vez)

- **GitHub**: o workflow `.github/workflows/narrado.yml` já vem no repositório. Cada vídeo vira uma release `narrado-<id>` (pode apagar as antigas quando quiser).
- **Vercel → Settings → Environment Variables**:
  - `GH_TOKEN`: token *fine-grained* do GitHub só para este repositório, com **Contents: Read and write** e **Actions: Read and write**.
  - `NARRADO_SENHA`: uma senha qualquer; a tela pede uma vez (⚙) e guarda no navegador. Sem ela, qualquer pessoa com o link poderia disparar renders.
- Na tela, em ⚙, informe a chave do Gemini (se ainda não usou no editor) e a senha.

Arquivos: `narrado.html/js/css` (tela), `api/` (funções do Vercel), `narrado/` (motor em Python que roda no Actions).
