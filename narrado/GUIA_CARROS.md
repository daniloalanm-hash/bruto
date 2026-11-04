# Guia — vídeos de carros (Modo Narrado, YouTube)

Leia isto antes de mexer nos vídeos de carros. O sistema é o mesmo do Jogando Sem Plano (games); o canal de
carros é um "nicho" dentro dele. Tudo que muda em comum vale para os dois canais.

## O que é

Canal: **Radar de Garagem** (youtube.com/@RadardeGaragem). Slogan: "Seu próximo carro pode estar no radar".
Logo em `narrado/marca/radardegaragem.png` (marca d'água + logo na fala do nome do canal e no fim).

Vídeo longo para YouTube (horizontal, 10 min+), sem rosto: roteiro pesquisado + narração do Danilo (ou voz de
IA para teste) + vídeos oficiais dos carros por baixo. Referência de formato: canal "Carros. com"
(ex.: "5 Carros INJUSTIÇADOS que SUPERAM os MAIS CAROS").

## Vídeos de referência (canal "Carros. com")

Antes de planejar um vídeo, (re)veja estes — o canal de carros segue esta fórmula:

1. **5 Carros INJUSTIÇADOS que SUPERAM os MAIS CAROS 😱: IMPOSSÍVEL ACREDITAR no que eles OFERECEM**
   — https://www.youtube.com/watch?v=xNorSEoqxqw (30 min, ~25 mil views na análise de out/2026)
2. **5 SUVs QUE TODO BRASILEIRO IGNORA, MAS QUE HUMILHAM OS CONCORRENTES | TODOS ABAIXO DA FIPE**
   — https://www.youtube.com/watch?v=vmR02ZRKP9c (22 min, ~67 mil views na análise de nov/2026). Modelos na
   tela: Toyota RAV4, Honda, Jeep Cherokee, Mercedes GLA, Suzuki S-Cross.

O que os dois têm em comum (a fórmula):
- **Ângulo de "achado"/custo-benefício**: carros (muitas vezes usados) que entregam mais do que custam,
  "ignorados", "injustiçados", "abaixo da FIPE", "humilham os concorrentes". Lista de 5 modelos.
- **Título em hype com palavras em MAIÚSCULAS** (INJUSTIÇADOS, SUPERAM, HUMILHAM, ABAIXO DA FIPE) + emoji às vezes.
- **Só imagem oficial/de divulgação do carro**: rodando em estrada, closes (farol, roda, lanterna), interior,
  multimídia, câmera de ré. Sem apresentador. Narração o tempo todo, música baixa.
- **Faixa vermelha com o nome do modelo** quando ele entra (a nossa ficha) e **palavra de impacto grande** na
  abertura ("ACHADOS?", "USADOS").
- Marca d'água do canal no canto superior direito.
- Longos: 20–30 min (5 carros com bastante detalhe: motor, consumo, itens, manutenção, preço de usado/FIPE).

## Regras do Danilo (não quebrar)

- **Apresentação fixa do canal no fim da introdução** (padrão de todo vídeo de carros), sempre com este texto:
  "Esse é o Radar de Garagem. Aqui você descobre quais carros realmente valem o que custam, quais modelos
  surpreendem pelo que oferecem e o que você precisa saber antes de escolher seu próximo automóvel."
  Na tela: logo do canal (quando a narração fala "Radar de Garagem", `cta.momentos_canal`) e logo depois o
  "inscreva-se + sino" (`cta.momentos_todos`). No app: `NICHOS.carros.apresentacao` + `comApresentacao()`
  (vale para roteiro gerado e para roteiro pronto); a IA é instruída a não escrever outra apresentação.

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

## Roteiro escrito fora do app (pronto para gravar)

Quando o roteiro é escrito no chat (seguindo a predefinição editorial do Danilo), ele vai para
`narrado/roteiros/<id>.json` no formato `{id, criado, materia: null, roteiro: {titulo, titulo_tela, formato:
"horizontal", nicho: "carros", jogos, cenas, post, thumbs}}` (cenas iguais às de `testes/carros_10min.json`:
`capitulo` + `ficha` na 1ª cena de cada carro, `assunto`, `numero`, `destaques`). O Danilo abre
`narrado.html?roteiro=<id>`: o app põe no lote, troca para YouTube + carros e abre no passo do roteiro, com
teleprompter na voz. Primeiro: `suvs-injusticados-fipe` (5 SUVs injustiçados na FIPE, nov/2026).

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
4. ~~Logo e marca d'água do canal de carros~~ — feito: `render.LOGOS["radardegaragem"]`, `cta.CANAIS`,
   `marcaCarros()` no app (marca = "@" + nome do canal sem espaços).
5. A etapa "Cortando erros e repetições" demora ~12 min com voz de IA (transcrição) — dá para pular no modo IA.

## Histórico dos testes

- `carros_ia` (3 carros, 2:53): validou material, ficha, números, cama.
- `carros_10min` ("6 carros que reinaram no Brasil", ~9:30): Gol, Onix, Corolla (+Cross), i30, Compass,
  Strada. Corrigidos: van Transporter no lugar do Gol; selo "-22%" falso em comparação que não era desconto.

## Lições do 1º teste (SUVs injustiçados, nov/2026)
- Título do vídeo-fonte precisa ter a **marca** e um **ano dentro da geração** da ficha. Sem isso entrou trailer
  de filme ("Territory"), queimada na Austrália e o C4 Cactus 2014 (carroceria que não é a vendida aqui).
- "All-new"/"nova geração" no último ano da faixa é a geração seguinte: fora.
- A faixa `geracao` da ficha usa os anos mundiais da geração (revelação → troca), não só os anos de Brasil.
- Cada trecho passa pelo CLIP: o que não é o carro (gente, mão no volante, desenho do designer, paisagem,
  cena de filme, legenda) sai do banco; o resto ganha a vista (interior, painel, rodas…) que a fala pede.
- Na abertura, fala com preço de um carro mostra esse carro.

## Lições do 2º teste
- O CLIP olhando só o quadro do meio deixava passar apresentador, mão no volante e igreja no fim do plano.
  Agora confere **1 quadro por segundo** de cada trecho, com uma segunda pergunta só de 3 grupos (gente /
  sem carro / carro). Quadro reprovado corta o trecho (fica o maior pedaço limpo, com 0,3 s de folga).
- Fotos do Commons também passam pela geração: ano do modelo no nome ou nas categorias (datas da foto
  como "2019 in Paris" não contam). Sem foto da geração certa, fica sem foto.
- Teste com voz de IA publica `<nome>_vistas_debug.jpg/.json` (nota de cada quadro) para calibrar.

## Lições do 3º teste
- O "gente" do CLIP não separa: apresentador dá 0,3–0,45 e porta com acabamento de madeira também. Gente
  agora é pelo **detector YOLOX** ("person" ≥ 0,45, caixa de 0,8% a 90% da tela), quadro a quadro, **2 por
  segundo** (vinheta de outro canal no meio do plano escapava com 1 por segundo).
- Foto do Commons só entra se o carro tiver menos de 90 s de vídeo limpo. "Cactus (2019) (flickr id)" era o
  ano da FOTO de um Cactus 2014. Foto de rali/conceito/corrida ("3008 DKR") nunca entra.
- Cartões automáticos de número (lidos da fala) **desligados** no canal: deram "138.900 na tabela FIPE"
  (era o preço de lançamento) e "3.008" (nome do carro). Só os cartões do roteiro, com rótulo conferido.
- Abertura que fala o preço de um carro: cada plano dele entra uma vez; acabou, a montagem segue com os outros
  carros (o Territory em loop por 15 s parecia defeito).
