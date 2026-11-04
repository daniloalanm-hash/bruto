# Guia — vídeos de carros (Modo Narrado, YouTube)

Leia isto antes de mexer nos vídeos de carros. O sistema é o mesmo do Jogando Sem Plano (games); o canal de
carros é um "nicho" dentro dele. Tudo que muda em comum vale para os dois canais.

## O que é

Vídeo longo para YouTube (horizontal, 10 min+), sem rosto: roteiro pesquisado + narração do Danilo (ou voz de
IA para teste) + vídeos oficiais dos carros por baixo. Referência de formato: canal "Carros. com"
(ex.: "5 Carros INJUSTIÇADOS que SUPERAM os MAIS CAROS").

## Regras do Danilo (não quebrar)

- **Nunca citar outros canais, youtubers, streamers ou influenciadores** no roteiro (nem como repercussão).
- **Sem o som original dos vídeos** dos carros (música de comercial gera reivindicação no YouTube). Só a voz e a
  cama sonora **suave** (clima `suave` em `efeitos.py`, sem batida).
- Mostrar só o carro de que se está falando. Nada de apresentador/pessoas na tela, nada de tela com texto.
- Sem legenda queimada (limita a entrega geográfica); sem pop/zoom nas palavras.
- Vídeos acima de 10 min (monetização). Voz de IA fala ~3 palavras/s; o Danilo, menos — ~1.500 palavras
  dão ~9,5 min com IA e passam de 10 min com a voz dele.
- Roteiro com tom de conversa (história, perguntas, revelação aos poucos), nada de "Além disso",
  "Vale ressaltar", fatos empilhados. Só fatos pesquisados, com fonte; nada inventado.
- Correções valem para os vídeos futuros (mudar o sistema, não só o vídeo).

## Como funciona (onde está cada coisa)

- **App** (`narrado.html` / `narrado.js`): na caixa do YouTube, campo **Canal** → "Canal de carros" + nome do
  canal (salvo no navegador). Isso define `nicho: "carros"` no roteiro e no job (`montarJob`).
  - Prompts por nicho: objeto `NICHOS` em `narrado.js` (canal, fontes de pesquisa, ficha, tipos de vídeo).
  - Ficha de carro: `{carro, ano, motor, potencia, consumo, preco}` (`normFicha`, `fichaEditor`).
  - A pesquisa (`promptPesquisa`) usa a matéria/tema como âncora e busca montadoras, imprensa automotiva,
    FIPE, Inmetro. O roteiro é escrito numa chamada só (`promptRoteiroInteiro`); capítulo a capítulo é plano B.
- **Servidor** (GitHub Actions `narrado.yml` → `narrado/pipeline.py`):
  - `definir_nicho()` liga `NARRADO_NICHO=carros`.
  - Material: `assets.baixar_carro()` → `_dailymotion_carro()` (busca "exterior design", "interior design",
    "driving", "official"; prefere 720p+ e canais de montadora) + fotos de licença livre do Wikimedia Commons.
    Baixa **sem áudio**.
  - Casamento do modelo: `assets.carro_no_titulo()` — todas as palavras do modelo como palavra inteira
    (Gol ≠ Transporter; Corolla ≠ Corolla Cross; "Gol" exige a marca no título).
  - Filtro de título: `RUIM_CARRO` (crash test, brinquedo, jogo de corrida, comparativo…), com palavra inteira
    (já houve bug de "toy" barrando "Toyota").
  - Análise dos trechos (`trechos_corridos`): descarta texto (EAST), preto e **pessoa** (rosto YuNet, só carros).
  - Faltou vídeo de um carro → busca mais (`_dailymotion_carro(extra=True)`).
  - Render: `som_original: False`, cama `suave`, ficha vermelha (`cartoes.ficha`/`linhas_da_ficha`),
    números animados (`grafico.py`), revisão automática (`qa.py`), thumbs (`thumbs.py`, prefere quadro do vídeo
    oficial à foto do Commons, que pode ser de outra geração).

## Testar sem o app (voz de IA)

1. Escreva o job em `narrado/testes/<nome>.json` (veja `carros_10min.json`: título, `nicho: "carros"`,
   `voz: {"modo": "ia", "voz_ia": "pt-BR-AntonioNeural"}`, cenas com `capitulo`, `ficha`, `numero`, `assunto`).
2. Dispare: `gh api -X POST repos/daniloalanm-hash/bruto/actions/workflows/narrado.yml/dispatches -f ref=main
   -f inputs[tag]=narrado-mvmuuad6a9dd -f inputs[fase]=teste_job_<nome>`
3. Resultado na release `narrado-mvmuuad6a9dd` com prefixo: `<nome>_final.mp4`, `<nome>_thumb_1..3.jpg`,
   `<nome>_projeto.json` + `log_servidor.txt`. Não mexe no projeto da release.
4. Só a busca de material: fase `teste_carros` (publica `carros_teste.jpg` com um quadro de cada trecho).

Restrições desta sessão de trabalho: não usar a senha do app (NARRADO_SENHA), não criar/editar releases à mão
(só pelo workflow), não pedir nem digitar credenciais. O Gemini do roteiro roda com a chave do Danilo, no
navegador dele — para testar roteiro gerado pela ferramenta, ele cola o texto no chat.

## Pendências conhecidas (próximos passos)

1. Preferir vídeo oficial de montadora a comercial de TV antigo (ex.: propaganda do Onix com galinha entrou).
2. Filtro de pessoa pega só rosto de frente: gente de corpo inteiro/perfil ainda escapa (YuNet → detector de
   pessoa).
3. Alguns modelos têm pouco vídeo bom (Compass, Strada antiga) → trechos repetidos (a revisão aponta).
   Próxima fonte: sites de imprensa das montadoras (Stellantis, VW, Toyota, Hyundai, GM…).
4. Logo e marca d'água do canal de carros (quando o Danilo mandar a logo): `render.LOGOS` e `cta.CANAIS`.
5. A etapa "Cortando erros e repetições" demora ~12 min com voz de IA (transcrição) — dá para pular no modo IA.

## Histórico dos testes

- `carros_ia` (3 carros, 2:53): validou material, ficha, números, cama.
- `carros_10min` ("6 carros que reinaram no Brasil", ~9:30): Gol, Onix, Corolla (+Cross), i30, Compass,
  Strada. Corrigidos: van Transporter no lugar do Gol; selo "-22%" falso em comparação que não era desconto.
