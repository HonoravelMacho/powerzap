# PROMPT PARA OUTRA IA — CORRIGIR O SELETOR VISUAL DE CONTATOS DO POWERZAP

> **Contexto da transferência:** projeto PowerZap, app de agendamento de mensagens
> WhatsApp (Flet 0.24.1 + Evolution API). O **único problema em aberto** é o
> "seletor visual de contatos": a lista de contatos/grupos **não aparece na tela**,
> mesmo após ~10 versões tentando corrigir. 100% das funcionalidades restantes
> funcionam (backend, envio de arquivos, QR/conexão, agendamento, scheduler, CI/CD).
>
> **Objetivo da sua missão:** fazer o seletor visual mostrar a lista e permitir
> escolher contato/grupo, agendando mensagem **para o número 555189213483**
> (número do usuário) pelo fluxo: Nova mensagem → ícone verde → escolher → salvar
> com data futura → "pendente" no calendário → envio automático pelo scheduler.

---

## 1. REGRAS ABSOLUTAS (do AGENTS.md do projeto)

- **Responda SEMPRE, exclusivamente, em Português do Brasil.**
- Comentários, commits e mensagens de terminal em PT-BR.
- Não use emojis.
- Só commite/publique quando o usuário pedir ou quando fizer parte do fluxo
  de release combinado (abaixo). Nunca rode `git config`, nunca force-push.

---

## 2. AMBIENTE E ACESSOS

| Item | Valor |
|---|---|
| Repositório | `https://github.com/HonoravelMacho/powerzap.git` (era `powerzap-`) |
| Diretório de trabalho | `/home/tiagorabelo/powerzap` |
| Sistema | Linux (Pop!_OS, X11, display `:0.0`), shell `zsh` |
| Flet | **`flet==0.24.1`** (fixado em requirements.txt) |
| Evolution API | Docker local `localhost:8080`, `apikey=powerzap`, instância `powerzap` (WhatsApp conectado, sessão persistente) |
| Banco de dados | `~/.local/share/powerzap/powerzap.db` — **≈2.175 contatos, 158 grupos** |
| Número do usuário (sender/destino de teste) | `555189213483` |
| Senha sudo do usuário (para instalar .deb) | (somente local — nunca versionar) |
| Compatado automaticamente | `powerzap-scheduler --interval 20` (envia agendados) |
| Log de runtime | `~/.local/share/powerzap/error.log` (usar `crashlog.debug`) |
| Build | GitHub Actions ao criar tag `vX.Y.Z` → `.deb/.rpm/.exe/.tar.gz` |
| Versão instalada hoje | `0.3.10` |

> ⚠️ **SEGURANÇA:** nunca registre tokens do GitHub ou senhas neste arquivo.
> Se um segredo for exposto por acidente, rotacione-o imediatamente no GitHub.

---

## 3. ARQUITETURA ATUAL (não quebrar o resto)

- `powerzap/main.py`: `page.add(ft.Row([NavigationRail, content Container(expand=True)]))`
  → **o app NÃO usa `page.views`** (usar views quebra e reabre o calendário).
- `main.py` expõe no page: `page.powerzap_content = content` e `page.powerzap_views = views`.
- Navegação por rail: `switch(idx)` faz `content.content = views[idx]; page.update()`.
  **Isso funciona** para Calendário/Conexão/Etiquetas/Ajustes.
- O seletor usa o MESMO mecanismo: `MessageDialog._show_picker()` (calendar_view.py:474)
  fecha o diálogo, cria `ContactPickerView(self.page_ref, ...)`, faz
  `page.powerzap_content.content = picker; page.update()`.
- **CalendarView renderiza perfeitamente e é a REFERÊNCIA de como a UI deve ser**
  (Column `expand=True, spacing=0` com `controls = [header Container, Divider, body Row(expand=True)]`).

### Estrutura ATUAL do ContactPickerView (calendar_view.py:35)
- `class ContactPickerView(ft.Column)` — `expand=True, spacing=0`.
- `self.controls = [header Container, Divider, top_panel Container, list_panel Container, bottom_panel Container]`
- `self.list_box`: `ft.Container(height=380, border=green, borderRadius, padding, bgcolor=SURFACE)`
  → conteúdo `ft.ListView(cards, spacing=0, padding=4, expand=True)`.
- Cartões: `ft.Container(margin, padding=10, border_radius=8, bgcolor=GREY_800, ink=True,
  on_click, content=Column([Text("nome • tipo"), Text(número, AMBER_200)]))`.
- **Lista única combinada** contatos+grupos, **ordem alfabética A-Z**, filtro por digitação
  (`_filter`); botão "Você (meu número)" chama `_pick_own()`.
- Sincronização agendada: `threading.Timer(3.0, lambda: self._safe_call(page, self._sync)).start()`
  → roda na **thread da UI** via `page.run_thread`.

---

## 4. FATOS DIAGNÓSTICOS JÁ COMPROVADOS (não refazer)

1. **Os dados carregam.** error.log em toda abertura mostra:
   `picker: cache 2175 grupos 158` e `picker render: 300 cartões -> ListView novo`.
   Nenhuma exceção Python em nenhum caminho do seletor (cada item tem try/except
   que logaria `picker item erro`).
2. **A serialização do controle funciona.** Testado chamando
   `picker.build_update_commands(index, commands, [], [])` com uids manuais
   (`_Control__uid`) + `index={'page': objeto}`: OK, `add=5, set=1`
   (os 300 cartões vão DENTRO do comando add do painel). Não há TypeError de build.
3. **`page.update()` é chamado na thread certa** desde v0.3.10 (via `run_thread`).
   Antes, `_sync` rodava em `threading.Thread` e chamava `page.update()` direto —
   isso foi corrigido (v0.3.10), mas **o bug persistiu**.
4. **Captura de tela real (única obtida):** o tema escuro-verde M3, botões e textos
   do seletor RENDERIZAM; a área da lista fica VAZIA (sem cor de cartão GREY_800,
   sem texto âmbar, sem contador ciano). Espera-se o mesmo no app real do usuário.
5. `powerzap --diagnose` demonstra banco+API+render OK — **o bug é 100% de UI**.
6. O **diálogo de formulário (AlertDialog) RENDERIZA** — o usuário vê os campos e
   clica no ícone verde dentro dele toda vez. É a superfície mais garantida do app.

---

## 5. HISTÓRICO COMPLETO DE TENTATIVAS (NÃO REPETIR AO PÉ DA LETRA)

| Versão | Abordagem | Resultado |
|---|---|---|
| v0.2.7/8 | `ListView` e `Column(scroll)` dentro de AlertDialog | Lista vazia |
| v0.2.9 | Diálogo filho separado (AlertDialog aninhado) | Nem abre |
| v0.3.0/1 | Trocar `self.content` do MESMO AlertDialog | Abre, mas área vazia |
| v0.3.2 | `page.views.append(ft.View)` | Reabre calendário (page.add ignora views) |
| v0.3.3/4 | Swap de `content.content`; `ListView`/`ListTile` em Container | Janela grande abre, lista branca |
| v0.3.5 | `ListTile` nativo + contador de diagnóstico | Conta 300+2164, ainda branco |
| v0.3.6 | **Causa raiz nº1 encontrada:** `RoundedRectangleBorder(radius=8, side=...)` → essa versão não aceita `side`. Cada item lançava TypeError engolido. Corrigido | passou de "1 item" para "300 itens" — **mas tela continua branca** |
| v0.3.7 | Recria um `ListView` NOVO por render (não muta `.controls`) | Continua branco |
| v0.3.8 | Estrutura plana idêntica ao CalendarView | Continua branco |
| v0.3.9 | Altura **fixa 380px** na lista (evita colapso `expand` em Row) + lista única A-Z + rebuild pós-attach | Continua branco |
| v0.3.10 | **Nenhuma atualização de UI fora da thread principal** (run_thread) | Continua branco |

Ponto-chave: **nenhuma variação de widget de lista (ListView/ListTile/Container cards,
altura fixa/expand) fez os itens aparecerem** quando o picker é montado via
swap de `content.content`. Porém o MESMO tipo de card/ListView **funciona dentro do
CalendarView** (painel de detalhes usa `ft.ListView(cards, expand=True)` dentro de
`Container(width=340, no expand)` dentro de `Row(expand=True)`).

---

## 6. PISTAS FORTES PARA A PRÓXIMA FASE (investigar nesta ordem)

### A) O que o CalendarView tem que o picker não tem?
- `CalendarView.reload()` **só reconstrói a lista depois de já estar anexado** à
  árvore da página. O picker constrói a lista DURANTE `__init__`, antes do attach.
  Compare as duas árvores serializadas (`build_update_commands`) e o timing.
- Teste forese "degradado": montar o picker e, **SEM nenhum `update()` durante a
  montagem**, fazer UM ÚNICO `page.update()` após `content.content = picker`.
  Se funcionar → o problema é atualização intermediária de controle desanexado.

### B) Tente trocar `ListView` por `ft.Column(scroll=ft.ScrollMode.AUTO)`
- A grade do calendário é uma **Column** e renderiza. Um `ListView` pode estar com
  bug específico nesse contexto. Cartões em Column com scroll dentro de Container
  de altura fixa é uma alternativa de baixo risco.

### C) Coloque a lista DENTRO do próprio AlertDialog (superfície garantida)
- O formulário (AlertDialog) **renderiza para o usuário**. Em vez de trocar o conteúdo
  da página inteira, aumente o conteúdo do diálogo para incluir busca + lista
  (altura fixa). Atenção ao erro conhecido "ListView Control must be added to the
  page first" — isso acontecia por chamar `update()` antes do attach; evite updates
  pré-attach e use altura fixa.

### D) Elimine o diálogo e o swap — torne o seletor a própria main()
- Criar um branch/protótipo `proto_seletor.py` que abre SÓ o picker como conteúdo
  inicial (sem AlertDialog, sem swap). Comparar render: se abrir OK, o problema
  está na interação diálogo+fechamento+swap; se não, é o picker em si.

### E) Flutter/Material internals (avançado)
- Verificar se `AlertDialog.modal=True` bloqueando o swap segue causando estado
  inconsistente após `page.close(self)` + `anchor.content = picker` no mesmo tick.
  Tente `page.close(self)` + `page.update()` **antes** de trocar o conteúdo, com um
  pequeno `page.run_thread` no meio.

---

## 7. PROCEDIMENTO DE RELEASE E TESTE COM O USUÁRIO

1. Editar código → `python3 -m py_compile <arquivo>` → testes headless:
   `HOME=/tmp/x python3 -u -c "..."` (usar HOME temporário para testar DB isolado;
   sempre `-u` para evitar stdout bufferizado).
2. Commit: `git add ... && git commit -m "fix: ..." && git push origin main`
3. Tag: `git tag v0.3.X && git push origin v0.3.X` (nova versão acima da instalada).
4. Acompanhar CI até `completed success` (API: `actions/runs?per_page=1`).
5. Baixar `.deb` (`powerzap_0.3.X_amd64.deb`, **~70 MB** — baixar com
   `wget`/curl em background e POLLAR o tamanho até ≥70 MB; download direto síncrono trunca).
6. Instalar: `sudo dpkg -i powerzap_0.3.X_amd64.deb` (digite sua senha quando pedir)
7. **Limpar o log antes do teste:** `rm -f ~/.local/share/powerzap/error.log`
8. Pedir ao usuário: "Nova mensagem → ícone verde" e perguntar O QUE ele vê
   (título? busca? contador ciano? cartões?). Coletar `cat ~/.local/share/powerzap/error.log`.
9. **NUNCA presumir renderização** — pedir feedback explícito.

> ⚠️ Nota de ambiente do agente: ao tentar abrir o app/UI do próprio agente para
> screenshots (xdotool/import), as janelas do Flet **muitas vezes não mapeiam**
> cabe à sessão após o lançamento (processo roda sem janela). É mais confiável
> combinar diagnóstico por log + feedback do usuário do que automação de tela.

---

## 8. ARQUIVOS-CHAVE

- `powerzap/views/calendar_view.py` — o bug: `ContactPickerView` (linha 35),
  `_show_picker` (linha 474), `_render` (linha 209), `_filter`, `_sync`,
  `_safe_call` (linha 149). Referência de que funciona: `CalendarView` (linha ~650)
  e `MessageDialog._show_form`.
- `powerzap/main.py` — `page.powerzap_content`/`page.powerzap_views` (linha 59),
  `switch()` (linha 67). `diagnose()` (linha ~87).
- `powerzap/evolution.py` — cliente Evolution API (find_contacts, fetch_groups,
  find_chats, fetch_owner_number, send_media).
- `powerzap/db.py` — SQLite; `filter_local`, `replace_contacts`, `normalize_number`.
- `powerzap/views/connect_view.py` — QR/conexão (referência de threads funcionando).
- `powerzap/scheduler.py` — envio agendado.
- `.github/workflows/release.yml` — CI/CD; `packaging/build_deb.sh` — .deb.
- `~/.local/share/powerzap/error.log` — log de runtime;
  `~/.local/share/powerzap/powerzap.db` — banco real.

---

## 9. CRITÉRIO DE SUCESSO (done-definition)

- Usuário abre "Nova mensagem → ícone verde" e **vê a lista** (contatos+grupos A-Z,
  com contador ciano e cartões com nome/número).
- Usuário escolhe **o próprio número 555189213483** (ou "Você (meu número)"),
  agenda para o futuro, e a mensagem sai do "pendente" e é **enviada pelo scheduler**
  sem intervenção manual.
- Nenhuma das funcionalidades existentes regride (envio de arquivos, QR, etiquetas, ajustes).

Boa sorte — o diagnóstico está todo aqui, o bug é de renderização do Flet 0.24.1
no contexto do seletor, e a referência que FUNCIONA é o CalendarView.