# Guia — thumbnails do YouTube (Modo Narrado)

Código: `narrado/thumbs.py` (fase `thumbs` do workflow refaz só as thumbs de um vídeo).
São 3 versões por vídeo longo, para o "Testar e comparar" do YouTube Studio.

## Regra de ouro: hype
Thumb tem que chamar o clique de longe: imagem CLARA, colorida, com personagem grande; texto curto, gigante,
inteiro e legível no celular. Thumb escura, de quadro de gameplay sem personagem ou com texto cortado não serve.

## De onde vem a imagem (ordem de preferência)
1. **Arte guardada no repositório**: `narrado/arte/<chave_do_jogo>/*.jpg` (key art e screenshots oficiais
   que o Danilo manda no chat). Vale +60 na nota. Jogo sem Steam (ex.: GTA VI) depende disto.
   Não guardar fan art com marca d'água de artista (ex.: deviantart) nem montagem com logo no meio.
2. Imagens que o Danilo sobe no app para o vídeo (`jogo: "proprio"`), +60.
3. Key art da Steam (`library_hero`), +25.
4. Screenshots e quadros dos trailers.
A nota (`_nota`) premia cor, contraste e nitidez e pune imagem escura (brilho médio < 85) e com texto.
Personagem com rosto grande ganha +35.

## Os 3 estilos
1. **Manchete** — 2 a 4 palavras gigantes (branco + destaque em amarelo), inclinadas, contorno preto, à
   esquerda; o maior rosto do jogo principal à direita.
2. **Lado a lado** — 3 fatias inclinadas: um jogo por fatia quando o vídeo é de vários jogos relevantes;
   vídeo de um jogo só usa as 3 melhores imagens DIFERENTES dele. Frase curta embaixo.
3. **Impacto** — close do personagem + UMA palavra gigante INTEIRA embaixo (nunca partida em volta do rosto).
   Gráfico só com sentido: palavra de queda (FIM, ACABOU, CAIU…) → linha vermelha despencando, palavra
   branca; palavra de recorde (HISTÓRICO, RECORDE, BILHÕES…) → linha verde subindo, palavra amarela.
   Gráfico pequeno, no canto oposto ao rosto. Palavra neutra: sem gráfico.

## Textos
A IA do roteiro escreve `thumbs: [{texto, destaque}]` (2–4 palavras; a 3ª vira uma palavra só).
Nada de citar outros canais/influenciadores. Loja dos Gifts nunca aparece.

## Refazer uma thumb pelo app
No "Pacote de postagem do YouTube", cada thumb tem um campo de texto e dois botões:
- **🔄 Outra imagem**: mesmo texto (ou o do campo), imagem diferente — as já usadas naquela thumb vão para o fim
  da fila (`projeto.thumbs_hist`), e a escolha é sorteada entre as boas.
- **Usar texto**: mesma imagem, texto novo do campo. Palavra entre `*asteriscos*` fica amarela.
Por baixo: `api/job.js` (acao `thumb`) grava `thumb_pedido.json` na release e dispara a fase `thumbs`;
`pipeline.so_thumbs` refaz só aquela (`thumbs.gerar(..., so=n, evitar=..., fixar=...)`), ~3 min.
