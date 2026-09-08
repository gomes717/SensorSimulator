# TODO

- [ ] Adicionar cálculo das métricas clínicas de CGM (TIR, TAR, TBR, GMI, CV, Mean Glucose) sobre as curvas "expected" e "received", tanto no app quanto na documentação de análise de resultados.
- [x] Implementar simulação de evento PISA (Pressure-Induced Sensor Attenuation). **Feito (2026-09):** ver detalhe no item PISA abaixo (itens 2 e 4 foram unificados).
- [x] Permitir que o MCU funcione a partir de um CSV em vez do modelo fisiológico. **Feito (2026-09):** nova fonte de dados `data_source` (0 modelo / 1 CSV) persistida em `sim_config` v2; upload em chunks pelas características `CSV control` (`5b2c000f`) + `CSV data` (`5b2c0010`) para a partição de flash externa `sim_csv_partition` (`firmware/.../src/csv_store.c`), com CRC-32 na confirmação; `model_thread` emite uma linha por tick (`int16` mg/dL, sem ruído de sensor — a gravação já traz ruído real), repetindo a janela ao terminar. Food Log CSV consumido em modo *report-only* (aparece no Food/Exercise Status via os slots de "instant food", não altera a glicose — não há modelo rodando). "Insert Food Now" / "Insert Exercise Now" continuam funcionando por cima. O Food Log é pareado automaticamente pelo ID do arquivo (`Dexcom_001.csv` ↔ `Food_Log_001.csv`, `food_log_csv.matching_food_log_path()`) — sem escolher arquivo. App: `api/protocol.py` (`encode_csv_*`, `build_*_track`), `services/ble_session.py` (`start_csv_upload`), botão "Assign window to person…" na janela CSV Analysis, "Send CSV to Board" na Configuration, e `models/engine.py` repete a mesma janela localmente para a linha "expected". Validação: `scripts/validate_ble_stream.py` + [`docs/BLE_PAYLOAD_VALIDATION.md`](BLE_PAYLOAD_VALIDATION.md).

- [~] Fazer o MCU transmitir como um Dexcom (formato BLE proprietário G6/G7) e o app saber ler os dois formatos, com o tipo de comunicação **configurável**. **Parcial (2026-09):** perfil configurável + stream Dexcom básico *sem autenticação* feitos — campo `comm_profile` em `sim_config` v4, característica `Comm profile` (`5b2c0014`), serviço `dexcom_service.c` (`0xFEBC`, nome `DXCM01`, mensagem realtime de 14 B `opcode|status|u32 seq|u32 ts|u16 glucose|state|trend`), combo "Communication type" na Configuration, decoder em `ble_session.py`. Verificado em hardware (round-trip SIG↔Dexcom, e2e S16). **Baseline reenquadrado (2026-09-08, issue 09):** a afirmação sustentada é "o firmware transmite por dois formatos de fio e o app decodifica ambos" — validada pelo S16-01 (no subset `SMOKE`); a mensagem está tabulada byte a byte em `PROTOCOL_SPEC.md`; a `.tex` não implica compatibilidade com transmissor real. **Falta (stretch, issue 12):** desafio AES do G6 + `layout` EGV real para um app de terceiro que fale com Dexcom ler a placa; backfill de 24 h; calibração.
  - **Firmware.** Além do serviço SIG CGMS padrão (`0x181F` / `0x2AA7`, o de hoje), expor um segundo perfil BLE que imita o transmissor Dexcom: serviço proprietário `FEBC` (base 128-bit `F8083532-849E-531C-C594-30F1F86A4EA5`), características de **Control** (`…3535`) e **Backfill/Data** (`…3538`), handshake de autenticação (J-PAKE no G7 / desafio AES no G6) ou o "test mode" sem autenticação, e mensagens de glicose com opcode (`opcode | status | sequence(4) | timestamp(4) | glucose(2, uint LE, 12 bits baixos) | state | trend`) mais o comando de backfill (opcode `0x59`, payload de 8 B com início/fim). Nome anunciado no padrão `DXCM..` / `DX02..`. Ver [`docs/BLE_PAYLOAD_VALIDATION.md`](BLE_PAYLOAD_VALIDATION.md) §2 (layout de referência) e `docs/FEATURE_IDEAS.md` #16.
  - **Configurável.** Novo campo `comm_profile` em `struct sim_config` (`0` = SIG CGMS, `1` = Dexcom-proprietário), persistido (bump de `SIM_CONFIG_VERSION`), escrito por uma característica no serviço de config atual (`5b2c00xx`, read+write, 1 B) — e/ou Kconfig de build. Trocar o perfil re-registra os serviços GATT e re-anuncia (aplica em reboot se troca em runtime for muito invasiva). Só um perfil ativo por vez (rádio único, `CONFIG_BT_MAX_CONN=1`).
  - **App.** `services/ble_session.py` passa a reconhecer os dois: decoder SIG (já existe, `_decode_cgm_measurement`) e um decoder Dexcom novo (serviço `FEBC`, parse das mensagens com opcode, backfill). Seleção na janela Configuration (combo "Tipo de comunicação: SIG CGMS / Dexcom") que escreve `comm_profile` no board e ajusta como o app faz o scan/parse. `docs/E2E_TEST_PLAN.md` ganha uma suíte para o perfil Dexcom (auth, mensagem realtime, backfill de 24 h).
  - **Esforço/risco.** XL / alto — handshake criptografado engenharia-reversa, fácil errar detalhe; fazer atrás de uma flag e manter o SIG CGMS como padrão.

- [x] Multi-sensor em uma placa (vários CGMs simultâneos). **Fases 1 e 2 feitas + verificadas em hardware (2026-09).** **Fase 2 (app):** janela **Board Layout** (`graphic/board_layout_window.py`, aberta por Configuration → "Board layout") atribui um Person + Sensor a cada slot, persistido em `data/board_layout.json` (`models/board_layout.py`). "Send layout to Board" → `BleSession.send_board_layout(slots)`: por slot escreve o cursor **Sensor select**, depois `person`/`sensor`/`data_source`/food+exercise (espaçado ~50 ms para a fila de config de 16 do firmware acompanhar), depois upload de CSV por slot quando a pessoa é CSV, e por fim run state RUNNING. `_require_pairing = False` para identidades numeradas — o `BleSession` pula o passo de pareamento do Windows (tentá-lo travava a pilha BLE num loop de connect/disconnect). Gráfico já plota a linha da árvore selecionada; conectar às 4 identidades dá 4 linhas. Verificado: layout de 4 modelos distintos + ruído misto lido de volta por slot exatamente, e `model_tick[0..3]` do serial passou a `model=0/1/2/3`. **E2E dedicado:** `scripts/e2e_4sensor.py` (7 casos, 7/7 PASS em hardware) — push de layout + readback por slot, demux por identidade, playback CSV num slot, fast mode com relógio compartilhado, Insert Food/Exercise/PISA direcionados a um slot. Ver `docs/E2E_TEST_PLAN.md` §9. **Fase 1 (firmware):** design de **N identidades BLE independentes** restaurado sob `CONFIG_APP_SENSOR_COUNT` (1–4, default 4); `N == 1` é idêntico ao build de sensor único. Cada slot tem seu `struct sensor_slot` totalmente independente (modelo + params + ruído + agenda, **ou** um CSV — misturável, ex. 3 CSV + 1 modelo); relógio e multiplicador de velocidade são **globais**. `sim_config` v5 (top-level: `magic/version/sensor_count/comm_profile/speed_mult` + `slots[4]`; o byte `mode` legado saiu da struct). Nova característica **Sensor select** (`5b2c0015`, 1 B, não persiste) escolhe qual slot as escritas/leituras por-sensor (person/sensor/data_source/food/exercise/CSV) atingem. `csv_store` re-particionado em 4 trilhas por slot. `main.c` cria identidades 1..N-1 com endereços estáticos fixos, um adv set + instância CGMS por identidade, nome `"Nordic Glucose Sensor {i+1}"`; `comm_thread` faz push por slot. **Pareamento (testado em hardware 2026-09):** **Opção B** — `CONFIG_BT_PRIVACY=n` (endereços estáticos, não RPA) + `CONFIG_BT_MAX_PAIRED=8` + segurança iniciada pelo periférico (`bt_conn_set_security` no `connected()`) — **falhou**: identidade 0 pareia e faz GATT criptografado normal, mas 1..N-1 dão `Security failed ... level 1 err 2` (`AUTH_REQUIREMENT`) e o Windows derruba o link (`reason 0x13`). Ativada a **Opção A**: `CONFIG_APP_CGMS_NO_AUTH` (Kconfig `default y if APP_SENSOR_COUNT > 1`) troca as permissões `*_AUTHEN` das características CGMS por `READ/WRITE` puras — as N identidades transmitem sem pareamento nenhum. **Verificado em hardware:** scan mostra "Nordic Glucose Sensor 1..4" em 4 endereços (`D0:3F:4D:E2:7C:9B`, `C1/C2/C3:11:5B:2C:9E:A1/A2/A3`), cada identidade conecta sem parear e transmite a glicose do seu próprio slot (97/99/101/103 mg/dL nos defaults, batendo com a linha `model_tick[i]` do serial). Build `=1` (com segurança real) e `=4` compilam; RAM 47%. **Falta: Fase 2** = UI de atribuição de slots (associar cada slot a um `PersonProfile`/CSV, enviar "layout" da placa) na janela de Configuração. Ver `PROTOCOL_SPEC.md` §7.
- [x] PISA como evento injetável "inserir agora". **Feito (2026-09):** característica `PISA instant` (`5b2c0013`, `u16 duration_min + f32 depth_frac`), slot `instant_pisa[]` em `model_thread.c` na mesma classe dos instant food/exercise (não persiste, não reseta o relógio). Atenua a *leitura do sensor* por `1 - depth*sin(pi*elapsed/duration)` — falso baixo suave, aplicado depois do modelo de ruído e também na fonte CSV (a glicose real não muda). `models/engine.py` (`add_instant_pisa`) espelha para a linha "expected"; botão "Insert PISA Now…" na janela principal; intervalo sombreado no gráfico (`MainWindow._pisa_spans` / `_draw_pisa_spans`). Verificado em hardware: bout de 40% / 10 min levou o valor transmitido de 100 a ~60 no meio e de volta a 100.
- [x] Multiplicador de velocidade contínuo x1–x1000. **Feito (2026-09):** substitui o "Fast mode" on/off. Nova característica `Speed` (`5b2c0012`, float32) persistida em `sim_config` v3; `dt_min = (1/60) * speed_mult` no `model_thread.c` e no `SimulationEngine`. Slider log-mapeado (0–1000 → x1–x1000) na janela de Configuração (`_on_speed_changed`, `encode_speed`/`decode_speed`). O byte `mode` antigo continua na struct por compat de fio mas é ignorado. Verificado em hardware (x10/x120/x1000 lidos de volta; `dt` no console escala).

## Bugs e melhorias pendentes (2026-09-03)

> Triados e detalhados em `.scratch/thesis-stabilization/` (ver `INDEX.md` +
> `spec.md`) após a sessão de grilling + revisão de arquitetura de 2026-09-08.
> Track A (correção) primeiro, com testes de regressão; Track B (UX/estrutura)
> num lote pré-escrita.

- [x] Usuário aparece conectado mesmo depois de desconectar. **Feito (2026-09-08,
  issue 06, commit 2438d17):** `BleMessageLog.device_disconnected` → `MainWindow`
  marca a linha "⚊ offline" (cinza, valor `—`); a próxima mensagem do mesmo
  `dev_id` limpa (reconexão). Verificado em hardware.
- [ ] Arrumar legenda dos gráficos.
- [x] Arrumar o multiplicador de tempo — em velocidades altas está quebrando a
  integração da EDO. **Feito (2026-09-08, issue 03, commit 8873dfd):** o firmware já
  fazia sub-step (`MODEL_SUBSTEP_MAX_MIN = 1.0` em `model_thread.c`); faltava
  espelhar no motor da aplicação. `ModelStepper._tick_model` agora roda o mesmo
  laço `nsub = ceil(dt_min / 1.0)` — os dois lados usam algoritmo e constante
  idênticos, então as linhas "expected" e "received" continuam alinhadas em
  qualquer velocidade. Verificado em hardware a x300 (pico ~405 mg/dL nos dois).
  Testes em `tests/test_engine_step.py` (sem sub-step, UVA/Padova colapsa a 0 a
  x1000). **Pendente (sub-item, firmware):** uma escrita em Speed passa pelo
  `apply_config_locked` completo (reseta `sim_clock` e limpa eventos pontuais) —
  tornar a velocidade um escalar "a quente" é uma correção separada.
- [ ] Arrumar o timeslot do BLE entre os "sensores" (multi-identidade).
- [ ] Quando a fonte for CSV, não mostrar o gráfico de comida. **Feito (2026-09-08,
  issue 08, commit 45a0892):** o gráfico é rotulado "Food log — report-only (CSV
  playback; does not drive glucose)" quando a fonte da pessoa ativa é CSV.
- [ ] Trocar as métricas de % para tempo.
- [ ] Melhorar a janela de configuração, que está confusa.
- [x] Validar o protocolo Dexcom. **Feito como baseline (2026-09-08, issue 09):**
  o formato de fio atual é um segundo formato não-SIG inventado neste trabalho
  (sem auth, sem `layout` EGV real); "validar" no sentido baixo = o próprio
  decoder faz round-trip (E2E **S16-01**, no subset `SMOKE`) + tabela byte a
  byte no `PROTOCOL_SPEC.md`. A `.tex` foi reenquadrada para não implicar
  compatibilidade com transmissor real. Interop com app de terceiro (AES G6 +
  EGV real) = **issue 12** (stretch, Fase 2).
- [ ] Deixar as regiões do gráfico mais transparentes.
- [ ] Mudar o avatar.
- [ ] Melhorar o alerta de glicose.
- [ ] Melhorar a visibilidade de qual pessoa/sensor está sendo apresentado no gráfico.
- [ ] Melhorar o E2E, que deixou passar muitos erros.
- [ ] Arrumar o modelo rodando na aplicação: hoje roda um único modelo para todos os usuários; deve haver um modelo por usuário.
- [ ] Arrumar a organização de pastas (`api`, `core`, `gui`, `services`, `models`, `utils`).
- [x] Arrumar o PISA — parece não estar funcionando. **Investigado (2026-09-08,
  issue 02):** PISA está correto — verificado no motor da aplicação (teste
  unitário + `ui_smoke` cenário E) e no firmware a x1 (novo caso E2E **S7-04**:
  rampa suave 101→61→recupera). A percepção de "não funciona" vem da velocidade:
  o decaimento de um evento pontual usa o `dt_min` cheio, então acima de ~x10 um
  episódio de vários minutos termina em 1–3 ticks do modelo e a cadência BLE
  (~2–5 s) mal o amostra — um único blip ou nada. O S7-03 antigo mascarava isso
  por só testar o Model Only. Correção de fundo (tornar eventos pontuais
  visíveis em alta velocidade) fica no item do multiplicador de tempo abaixo.
- [ ] Adicionar métrica global na janela de CSV Analysis.
- [ ] Tirar o "slot" das janelas de configuração — usar o nome do usuário ou qual sensor se quer configurar.
