# Barra Bot

Um bot pra minigame de **equilíbrio**: tem uma barra vertical, um **retângulo** que sobe e desce
sozinho (às vezes rápido, às vezes devagar) e um **quadradinho branco**. Se você **segura o mouse**,
o quadrado sobe; se **solta**, ele cai. O objetivo é manter o quadrado dentro do retângulo até o tempo acabar.

O Barra Bot faz isso por você: ele olha a barra na tela, descobre onde estão o quadrado e o retângulo
e decide a cada instante se **segura** ou **solta** o botão do mouse.

Ele não lê memória do jogo. É só **visão (pixels da tela) + um controlador simples**.

## Como funciona

**1. Ver a barra.** Uma thread própria captura a região da barra (com [`mss`](https://github.com/BoboTiG/python-mss))
umas dezenas de vezes por segundo:

- o **retângulo** é achado pela **borda colorida**. A cor dela vai de amarelo a verde (e fica mais escura ou clara) conforme o quadrado se aproxima, então o que vale é a *estrutura*: duas linhas largas (topo e base) ou as duas laterais, com cerca de 10% da altura da barra, centradas nela;
- o **quadrado** é o que há de mais claro dentro da barra (branco, ou verde-claro quando está dentro do retângulo);
- coisas que parecem cenário (manchas cheias, verde escuro, fora do centro) são descartadas.

**2. Decidir.** O quadrado tem *inércia*: sobe acelerando enquanto você segura e cai acelerando quando solta
(medi uns ±500 px/s² num vídeo do jogo). Reagir só à posição atual faz ele passar do ponto, então o controlador
olha pra frente:

```
erro = (posição do quadrado − alvo no retângulo) + (velocidade relativa) × antecipação
segura o botão se erro > zona morta
```

A velocidade vem de um ajuste de reta nos últimos ~70 ms de leitura. Tem histerese pra não ficar
apertando e soltando o tempo todo. O **retângulo é tratado como imprevisível** (muda de velocidade ao acaso),
então o bot não tenta prever o movimento dele.

**3. Nunca travar o botão.** Se segurar por mais de 1,4 s, ou por mais de 0,5 s com o quadrado parado
(sinal de que o jogo não está respondendo), ele solta à força e avisa na tela.

## Requisitos

- Windows 10 ou 11
- Python 3.10 ou mais novo
- Rodar **como administrador**
- O jogo em **janela ou tela cheia sem bordas**

## Rodando

```bash
git clone https://github.com/Goiabatriste06/barra-bot.git
cd barra-bot
python -m pip install -r requirements.txt
python barra_bot.py
```

Ou abra o **`iniciar.bat`** com *botão direito → Executar como administrador*.

> Se o `python` não for reconhecido, tente `py`.

## Configurando (só na primeira vez)

1. Com a barra na tela, abra **⚙ Ajustes**.
2. Leve o mouse ao **meio do topo** da barra e aperte `F7`.
3. Leve o mouse ao **meio da base** e aperte `F8`. A janela mostra a altura marcada e um contorno azul na tela.
4. Marque **Modo teste** e confira o radar da janela: ele deve desenhar o retângulo e o quadrado acompanhando o jogo, sem clicar nada.
5. Desmarque o modo teste.

A marcação fica salva em `config_barra_bot.json` quando você fecha o app.

## Jogando

1. Aperte **`F6`** pra ligar. O app sempre abre desligado.
2. Ele espera a barra aparecer e controla sozinho. Quando a barra some, solta o botão.
3. `F6` de novo pra desligar.

| Atalho | O que faz |
|--------|-----------|
| `F6` | liga / desliga |
| `F7` | marca o topo da barra |
| `F8` | marca a base da barra |

Dica: use o `F6` em vez de clicar no botão da janela. Clicar na janela do bot tira o foco do jogo.

## Ajustes

| Opção | Pra que serve |
|-------|---------------|
| **Antecipação (ms)** | o quanto ele "olha pra frente". Padrão 300. Quadrado oscilando demais? Diminua (~250). Demora a corrigir e passa direto? Aumente (350 a 450). |
| **Botão do mouse** | esquerdo (padrão) ou direito. |
| **Modo teste** | só observa, não clica. Bom pra conferir a detecção. |
| **Salvar foto do que o bot vê** | grava um `diagnostico.png` com a imagem exata da região lida. Ótimo pra investigar quando ele não acha a barra. |

Zona morta e alvo dentro do retângulo ficam no `config_barra_bot.json` (`zona_morta` e `alvo_pct`) se quiser mexer.

## Problemas comuns

| Sintoma | O que fazer |
|---------|-------------|
| "Vejo só uma parte da barra" | confira o contorno azul: ele precisa cobrir a barra inteira (e a largura, o retângulo todo). Se persistir, salve a foto de diagnóstico. |
| "Quadrado não responde" | o jogo não está recebendo o clique. Clique na janela do jogo pra dar foco e rode como administrador. |
| Passa do retângulo toda hora | ajuste a **Antecipação**. |
| Poucas leituras por segundo | feche programas pesados. Abaixo de ~60 leituras/s o controle piora. |

## Estrutura

```
barra-bot/
├── barra_bot.py       # detecção + controlador + interface (Tkinter) num arquivo só
├── iniciar.bat        # atalho pra Windows
├── requirements.txt
└── .gitignore
```

Dentro do `barra_bot.py`: `detectar()` e `achar_retangulo()` (visão), `Controlador` (decide segurar/soltar)
e `App` (interface, thread de captura e mouse).

## Limitações honestas

- Eu calibrei com um vídeo e prints de um jogo específico (1080p). Outro tema visual, resolução muito diferente ou um
  retângulo de outra cor podem exigir ajustes na detecção.
- O controle foi validado numa simulação com a física que medi. No jogo real, a **Antecipação** é o botão que mais muda o resultado.
- Se o jogo mudar o visual da barra, a detecção pode parar de achar o retângulo.

## Aviso

Isso automatiza input em jogos. Muitos jogos (Roblox incluso) têm regras contra automação e podem punir a conta.
**Use por sua conta e risco** e, de preferência, em conta que você não se importe de perder.
