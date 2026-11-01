# barra-bot

Bot pro minigame de pescaria em que você segura o botão do mouse pra subir um quadradinho branco e tenta manter ele dentro de um retângulo que fica andando pela barra. Segurar e soltar por minutos cansa, e eu errava bastante, então escrevi isso.

Ele só olha a tela e aperta o mouse. Não lê memória do jogo nem injeta nada.

## Como funciona

Uma thread captura a região da barra com [mss](https://github.com/BoboTiG/python-mss), umas dezenas de vezes por segundo, e acha duas coisas:

- o retângulo, pela borda colorida. A cor muda de amarelo pra verde (e escurece de noite) conforme o quadrado chega perto, então não dá pra procurar uma cor fixa. Eu procuro a forma: duas linhas largas (topo e base) ou as duas laterais, com mais ou menos 10% da altura da barra, no centro dela.
- o quadrado, que é a coisa mais clara dentro da barra.

Teve falso positivo com cenário (minimapa, vitória-régia), então descarto mancha muito cheia, verde escuro e o que estiver fora do centro.

Pra decidir, só a posição não adianta. O quadrado tem inércia: sobe acelerando enquanto você segura e cai acelerando quando solta, uns 500 px/s² pelo que medi num vídeo. Se o bot reage só ao que vê agora, passa do ponto. Então ele olha um pouco pra frente:

```
erro = (posição do quadrado - alvo no retângulo) + velocidade relativa * antecipação
segura o botão se erro > zona morta
```

A velocidade sai de uma reta ajustada nas últimas leituras (~70 ms). Tem histerese pra ele não ficar apertando e soltando sem parar. O retângulo muda de velocidade do nada, então não tento prever o movimento dele.

Pra ele não ficar com o botão preso: se segurar mais de 1,4 s, ou mais de 0,5 s com o quadrado parado, solta à força e avisa na janela.

## Instalando

Precisa de Windows 10 ou 11, Python 3.10+ e rodar como administrador (senão o jogo ignora o clique).

```
git clone https://github.com/Goiabatriste06/barra-bot.git
cd barra-bot
python -m pip install -r requirements.txt
python barra_bot.py
```

Se preferir, abre o `iniciar.bat` com botão direito > Executar como administrador. Ele instala as dependências e abre o app. Se o `python` não for reconhecido, troca por `py`.

O jogo tem que estar em janela ou tela cheia sem bordas.

## Primeiro uso

1. Com a barra na tela, abre os Ajustes.
2. Leva o mouse no meio do topo da barra e aperta F7.
3. Leva o mouse no meio da base e aperta F8. Vai aparecer um contorno azul na tela mostrando a região lida.
4. Liga o modo teste e olha o radar na janela. Ele tem que desenhar o retângulo e o quadrado acompanhando o jogo, sem clicar em nada. Quando estiver certo, desliga o modo teste.

A marcação fica salva em `config_barra_bot.json` quando você fecha o app.

Pra jogar: F6 liga, F6 desliga. O app sempre abre desligado. Ligado, ele espera a barra aparecer, controla sozinho e solta o botão quando a barra some. Usa o F6 em vez de clicar no botão da janela, porque clicar nela tira o foco do jogo.

Atalhos: F6 liga/desliga, F7 marca o topo, F8 marca a base.

## Ajustes

- Antecipação (ms): quanto ele olha pra frente. Padrão 300. Se o quadrado fica balançando demais, tenta uns 250. Se demora pra corrigir e passa direto, sobe pra 350 ou 450. É o ajuste que mais muda o resultado.
- Botão do mouse: esquerdo por padrão.
- Modo teste: só observa, não clica.
- Salvar foto do que o bot vê: grava um `diagnostico.png` com a imagem exata que ele está lendo. Ajuda quando ele não acha a barra.

Zona morta e alvo dentro do retângulo estão no `config_barra_bot.json` (`zona_morta` e `alvo_pct`) se você quiser mexer.

## Se não funcionar

- "Vejo só uma parte da barra": o contorno azul precisa cobrir a barra inteira. Se continuar assim, salva a foto de diagnóstico e olha o que ele enxerga.
- "Quadrado não responde": o jogo não está recebendo o clique. Clica na janela do jogo pra dar foco e confere se está como administrador.
- Passa do retângulo toda hora: mexe na antecipação.
- Poucas leituras por segundo: fecha programa pesado. Abaixo de uns 60 por segundo o controle piora.

## Arquivos

- `barra_bot.py`: tudo num arquivo só. `detectar()` e `achar_retangulo()` fazem a visão, `Controlador` decide segurar ou soltar, `App` cuida da interface (Tkinter), da thread de captura e do mouse.
- `iniciar.bat`: atalho pra Windows.
- `requirements.txt`

## O que eu sei que está fraco

Calibrei com um vídeo e alguns prints de um jogo só, em 1080p. Se o seu tiver outro tema, outra resolução ou um retângulo de outra cor, a detecção pode precisar de ajuste. O controle eu testei numa simulação com a física que medi, e no jogo de verdade o que sobra pra acertar é a antecipação. Se o jogo mudar o visual da barra, a detecção provavelmente quebra.

## Aviso

Isso automatiza input em jogo, e muitos jogos (Roblox incluso) proíbem. Dá pra tomar ban. Use numa conta que você não se importe de perder.
