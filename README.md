# Melitta Barista TS Smart для Home Assistant

Интеграция для управления кофемашинами **Melitta Caffeo Barista TS Smart** и
**Barista T Smart** по Bluetooth LE — напрямую, без облака и без приложения
Melitta Connect.

[English version below](#english)

## Возможности

* Приготовление всех 24 встроенных напитков (21 на Barista T) — кнопкой или через сервис
* Приготовление напитка с произвольными параметрами: объём, крепость, температура, отсек для зёрен, молоко
* Приготовление двух чашек подряд
* Отмена приготовления и подтверждение запросов на дисплее
* Состояние: готова / приготовление / очистка / удаление накипи, текущая операция и прогресс в процентах
* Диагностика: пустой бак для воды, пустые отсеки для зёрен (оба на TS), поддон, необходимость Easy Clean
* Программы обслуживания: промывка, Easy Clean, интенсивная очистка, удаление накипи, установка/замена/снятие фильтра, выключение
* Настройки: жёсткость воды, энергосбережение, промывка при включении, автовыбор отсека для зёрен (TS), температура заваривания, таймер автовыключения, часы
* Счётчики: общий и по каждому напитку
* Профили пользователей — приготовление с сохранёнными в кофемашине настройками профиля

## Требования

* Home Assistant 2025.2 или новее
* Bluetooth-адаптер на хосте Home Assistant **или** [ESPHome Bluetooth Proxy](https://esphome.io/projects/?type=bluetooth)
  рядом с кофемашиной (обычно работает надёжнее)
* Кофемашина Barista T Smart или Barista TS Smart

## Установка

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Добавьте `https://github.com/david-dvinskykh/hass-melitta-barista-ts`, категория **Integration**
3. Установите **Melitta Barista TS Smart** и перезапустите Home Assistant

### Вручную

Скопируйте каталог `custom_components/melitta_barista_ts` в `config/custom_components/`
и перезапустите Home Assistant.

## Настройка

Включите кофемашину. Home Assistant обнаружит её автоматически и предложит
добавить в **Настройки → Устройства и службы**. Если этого не произошло,
добавьте интеграцию вручную: **Добавить интеграцию → Melitta Barista TS Smart**.

При первом подключении кофемашина может запросить подтверждение сопряжения —
подтвердите его на дисплее.

> Кофемашина принимает только одно Bluetooth-соединение. Если приложение
> Melitta Connect подключено, Home Assistant подключиться не сможет — закройте
> приложение.

### Параметры

Кнопка **Настроить** у интеграции:

| Параметр | По умолчанию | Описание |
| --- | --- | --- |
| Интервал опроса состояния | 5 с | Как часто читать состояние кофемашины |
| Интервал опроса счётчиков | 600 с | Как часто читать счётчики напитков (0 — не читать) |
| Подтверждать запросы автоматически | выкл. | Автоматически подтверждать запросы, для которых достаточно нажатия |

## Сущности

| Тип | Сущности |
| --- | --- |
| `sensor` | состояние, операция, требуемое действие, прогресс, всего напитков, состояние фильтра, счётчик по каждому напитку (по умолчанию отключены) |
| `binary_sensor` | приготовление, требуется действие, бак для воды пуст, поддон, отсеки для зёрен, требуется Easy Clean |
| `button` | приготовить, отменить, подтвердить запрос, промывка, Easy Clean, интенсивная очистка, удаление накипи, фильтр, выключить |
| `select` | напиток, профиль, температура заваривания |
| `number` | жёсткость воды, выключение через |
| `switch` | энергосбережение, промывка при включении, автовыбор отсека для зёрен (TS) |
| `time` | часы кофемашины |

Кнопка **Приготовить** готовит напиток, выбранный в `select` «Напиток», с
настройками профиля, выбранного в `select` «Профиль».

## Сервисы

### `melitta_barista_ts.brew`

```yaml
action: melitta_barista_ts.brew
data:
  device_id: a1b2c3d4e5f6
  recipe: cappuccino
  two_cups: false
```

### `melitta_barista_ts.brew_custom`

```yaml
action: melitta_barista_ts.brew_custom
data:
  device_id: a1b2c3d4e5f6
  process: coffee
  portion: 40
  milk_portion: 100
  intensity: strong
  temperature: high
  hopper: hopper_2
  name: Утренний
```

### Остальные

* `melitta_barista_ts.cancel` — остановить приготовление
* `melitta_barista_ts.confirm_prompt` — подтвердить запрос на дисплее
* `melitta_barista_ts.start_maintenance` — запустить программу обслуживания
  (`rinse`, `easy_clean`, `intensive_clean`, `descale`, `filter_insert`,
  `filter_replace`, `filter_remove`, `switch_off`)

## Пример автоматизации

Капучино по будням в 7:30, если кофемашина готова:

```yaml
automation:
  - alias: Утренний капучино
    triggers:
      - trigger: time
        at: "07:30:00"
    conditions:
      - condition: time
        weekday: [mon, tue, wed, thu, fri]
      - condition: state
        entity_id: sensor.melitta_barista_ts_smart_state
        state: ready
    actions:
      - action: melitta_barista_ts.brew
        data:
          device_id: a1b2c3d4e5f6
          recipe: cappuccino
```

## Диагностика

Если что-то не работает:

1. Проверьте, что приложение Melitta Connect не подключено к кофемашине.
2. Убедитесь, что кофемашина в зоне действия адаптера или ESPHome-прокси.
3. Включите отладочный журнал и приложите его к issue:

```yaml
logger:
  default: info
  logs:
    custom_components.melitta_barista_ts: debug
```

Скачайте диагностику устройства (**Устройство → ⋮ → Скачать диагностику**) —
она содержит расшифрованное состояние и значения регистров без адреса и
серийного номера.

## Протокол

Описание протокола — в [docs/protocol.md](docs/protocol.md). Он получен
обратной разработкой; официальной документации нет, и на других прошивках
поведение может отличаться.

---

<a name="english"></a>

# Melitta Barista TS Smart for Home Assistant

Home Assistant integration for **Melitta Caffeo Barista TS Smart** and
**Barista T Smart** coffee machines over Bluetooth LE — locally, with no cloud
and no Melitta Connect app.

## Features

* Brew all 24 built-in drinks (21 on the Barista T), from a button or a service
* Brew a drink with parameters of your own: volume, strength, temperature, bean
  hopper, milk
* Brew two cups in a row
* Cancel a preparation and confirm on-screen prompts
* State: ready / preparing / cleaning / descaling, current activity, progress
* Problem sensors: water tank empty, bean hoppers empty (both on the TS), drip
  tray, Easy Clean due
* Maintenance programs: rinse, Easy Clean, intensive cleaning, descaling, water
  filter insert/replace/remove, switch off
* Settings: water hardness, energy saving, rinse on start, automatic bean
  select (TS), brew temperature, auto-off timer, clock
* Counters: total and per drink
* User profiles — brew with the settings stored in a machine profile

## Requirements

* Home Assistant 2025.2 or newer
* A Bluetooth adapter on the Home Assistant host, **or** an
  [ESPHome Bluetooth Proxy](https://esphome.io/projects/?type=bluetooth) near
  the machine (usually more reliable)
* A Barista T Smart or Barista TS Smart

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/david-dvinskykh/hass-melitta-barista-ts` as an
   **Integration**
3. Install **Melitta Barista TS Smart** and restart Home Assistant

### Manual

Copy `custom_components/melitta_barista_ts` into `config/custom_components/`
and restart Home Assistant.

## Setup

Switch the machine on. Home Assistant discovers it automatically and offers it
under **Settings → Devices & services**. Otherwise add it by hand:
**Add integration → Melitta Barista TS Smart**.

On the first connection the machine may ask you to confirm pairing — confirm it
on the display.

> The machine accepts a single Bluetooth connection. While the Melitta Connect
> app is connected, Home Assistant cannot connect — close the app first.

### Options

| Option | Default | Description |
| --- | --- | --- |
| Status interval | 5 s | How often to read the machine status |
| Counter interval | 600 s | How often to read the drink counters (0 = never) |
| Confirm prompts automatically | off | Acknowledge prompts that only need a confirmation |

## Services

`brew`, `brew_custom`, `cancel`, `confirm_prompt` and `start_maintenance`. See
the Russian section above for examples, or the service pickers in
**Developer tools → Actions**.

## Troubleshooting

1. Make sure the Melitta Connect app is not connected to the machine.
2. Check that the machine is in range of the adapter or ESPHome proxy.
3. Enable debug logging and attach it to an issue:

```yaml
logger:
  default: info
  logs:
    custom_components.melitta_barista_ts: debug
```

The device diagnostics download contains the decoded status and register
values, with the address and serial number redacted.

## Protocol

The wire protocol is documented in [docs/protocol.md](docs/protocol.md). It was
obtained by reverse engineering; there is no official documentation and other
firmware revisions may behave differently.

## Credits

The protocol work of
[dzerik/melitta-barista-ha](https://github.com/dzerik/melitta-barista-ha)
(MIT) and the
[Home Assistant community thread](https://community.home-assistant.io/t/melitta-barista-ts-smart-coffeemachine/448204)
made this integration possible.

## Legal

Not affiliated with, endorsed by or connected to Melitta Group Management
GmbH & Co. KG or Eugster/Frismag AG. "Melitta", "Caffeo" and "Barista" are
trademarks of their respective owners and are used here for identification and
interoperability only. See [NOTICE](NOTICE).

Licensed under the [MIT License](LICENSE).
