#!/usr/bin/env python3

import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime


MODELS = [
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
]

API_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent"
)

# Размер одного текстового батча.
# Ограничиваем не токенами, а символами с большим запасом.
# Обычную встречу стараемся анализировать целиком.
# Если расшифровка всё-таки огромная, существующий
# механизм батчей продолжит работать.
MAX_BATCH_CHARS = 40000

# Несколько последних реплик предыдущего батча повторяются
# в следующем, чтобы не терять решения на границе.
OVERLAP_UTTERANCES = 8

CATEGORIES = [
    "Решение",
    "Договорились",
    "Поручение",
    "Зафиксировали",
    "Срок",
    "Риск",
    "Вопрос",
    "Следующий шаг",
    "Обсуждено без решения",
]


def log(message=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if message:
        print(f"[{timestamp}] {message}", flush=True)
    else:
        print(flush=True)


def get_key():
    p = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a", os.environ.get("USER", ""),
            "-s", "Gemini Transcribe API Key",
            "-w",
        ],
        capture_output=True,
        text=True,
    )

    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError(
            "Gemini API key не найден в macOS Keychain"
        )

    return p.stdout.strip()


def parse_transcript(path):
    text = path.read_text(encoding="utf-8")

    # Поддерживаем оба формата времени:
    #   MM:SS
    #   HH:MM:SS
    #
    # Имя спикера также может содержать двоеточие:
    #   Спикер 0
    #   spk:0
    #
    # Заголовок реплики определяется по времени в квадратных
    # скобках, поэтому speaker можно безопасно брать до конца строки.
    timestamp = r"(?:\d{2}:)?\d{2}:\d{2}"

    pattern = re.compile(
        rf"^\[({timestamp})–({timestamp})\]\s+"
        rf"(.+?):\s*\n"
        rf"(.*?)(?=\n\n\[{timestamp}–{timestamp}\]\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    rows = []

    for i, match in enumerate(pattern.finditer(text), 1):
        start, end, speaker, body = match.groups()

        body = body.strip()
        speaker = speaker.strip()

        if not body:
            continue

        rows.append({
            "id": f"R{i:06d}",
            "start": start,
            "end": end,
            "speaker": speaker,
            "text": body,
        })

    if not rows:
        raise RuntimeError(
            "Не удалось найти реплики в полной расшифровке. "
            "Поддерживаемые форматы:\n"
            "[00:00–00:10] spk:0:\\nтекст\n"
            "[00:00:00–00:00:10] Спикер 0:\\nтекст"
        )

    return rows


def row_for_prompt(row):
    return (
        f"{row['id']} | "
        f"{row['start']}–{row['end']} | "
        f"{row['speaker']} | "
        f"{row['text']}"
    )


def make_batches(rows):
    batches = []
    start = 0

    while start < len(rows):
        batch = []
        chars = 0
        i = start

        while i < len(rows):
            rendered = row_for_prompt(rows[i])
            extra = len(rendered) + 1

            if batch and chars + extra > MAX_BATCH_CHARS:
                break

            batch.append(rows[i])
            chars += extra
            i += 1

        if not batch:
            batch = [rows[start]]
            i = start + 1

        batches.append(batch)

        if i >= len(rows):
            break

        next_start = max(
            start + 1,
            i - OVERLAP_UTTERANCES,
        )

        start = next_start

    return batches


ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": CATEGORIES,
        },
        "text": {
            "type": "string",
            "description": (
                "Краткая точная формулировка результата "
                "без домыслов."
            ),
        },
        "responsible": {
            "type": ["string", "null"],
            "description": (
                "Ответственный только если он прямо следует "
                "из разговора. Иначе null."
            ),
        },
        "deadline": {
            "type": ["string", "null"],
            "description": (
                "Срок ровно в той форме, в которой он следует "
                "из разговора. Не вычислять календарную дату. "
                "Если срока нет — null."
            ),
        },
        "source_ids": {
            "type": "array",
            "items": {
                "type": "string",
            },
            "minItems": 1,
            "description": (
                "ID исходных реплик, непосредственно "
                "подтверждающих вывод."
            ),
        },
    },
    "required": [
        "category",
        "text",
        "responsible",
        "deadline",
        "source_ids",
    ],
    "additionalProperties": False,
}


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": ITEM_SCHEMA,
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


SYSTEM_RULES = """
Ты анализируешь ДОСЛОВНУЮ расшифровку рабочей встречи.

Твоя задача — извлечь только реально зафиксированные результаты
разговора.

КРИТИЧЕСКОЕ ПРАВИЛО:
НИЧЕГО НЕ ДОДУМЫВАЙ.

Правила:

1. Не назначай ответственного, если он прямо не следует из разговора.
2. Не придумывай срок.
3. Не превращай предложение или идею в принятое решение.
4. "Можно сделать" не означает "договорились сделать".
5. "Надо бы проверить" само по себе не является конкретным поручением.
6. Различай обсуждение и принятое решение.
7. Если ответственный неизвестен — responsible = null.
8. Если срок неизвестен — deadline = null.
9. Относительный срок сохраняй буквально:
   "до пятницы" -> "до пятницы".
   Не вычисляй календарную дату.
10. Каждый пункт обязан содержать source_ids тех исходных реплик,
    которые непосредственно подтверждают этот вывод.
11. Нельзя ссылаться на source_id, которого нет во входном тексте.
12. Обычные "да", "угу", повторы, шутки, приветствия, разговорный шум
    и незначимые реплики не превращай в пункты протокола.
13. Не создавай пункт только ради того, чтобы классифицировать каждую
    реплику. Большинство реплик вполне могут не породить ни одного пункта.
14. Если вопрос реально остался открытым, категория "Вопрос".
15. Если тему содержательно обсудили, но решения или договорённости
    не приняли, допустима категория "Обсуждено без решения".
16. "Решение" используй только когда из разговора следует, что решение
    принято.
17. "Договорились" используй только для явной договорённости.
18. "Поручение" используй, когда конкретному человеку или явно
    определяемому участнику поручено конкретное действие.
19. "Следующий шаг" — конкретное дальнейшее действие, которое следует
    из разговора, но не обязательно является поручением.
20. "Риск" — явно обсуждённый риск, ограничение или препятствие.
21. "Срок" используй как отдельный пункт только если срок сам по себе
    является существенным зафиксированным результатом. Если срок относится
    к поручению, лучше сохранить его в deadline поручения, не создавая дубль.
22. Формулируй text кратко, но не меняй смысл.
23. Если уверенного основания для пункта нет — НЕ СОЗДАВАЙ его.

ОСОБО О КОММИТМЕНТАХ И ОТВЕТСТВЕННЫХ:
- Не превращай предложение, идею, гипотезу или возможный вариант действия
  в принятое решение, договорённость, поручение или следующий шаг.
- Формулировки "можно", "можно сделать", "можно попробовать", "стоит",
  "наверное стоит", "надо бы", "хорошо бы" сами по себе НЕ являются
  обязательством действовать.
- "Договорились", "Поручение" и "Следующий шаг" требуют явного
  подтверждения действия/намерения: например "договорились", "сделаю",
  "буду", "беру", "да, сделаем", либо прямого поручения с принятием.
- Если вариант действий существенно обсуждался, но обязательство не принято,
  используй "Обсуждено без решения"; если это несущественно — не создавай пункт.
- Если участник прямо говорит от первого лица "я сделаю", "я отправлю",
  "я проверю", "я поставлю в копию" и это относится к будущему действию,
  responsible ОБЯЗАТЕЛЬНО укажи как speaker этой реплики (например
  "Спикер 4"), если настоящее имя из текста не установлено однозначно.
- Не оставляй responsible = null только потому, что имя человека неизвестно:
  технический идентификатор "Спикер N" является допустимым ответственным,
  когда действие явно взял на себя этот участник.

На входе могут повторяться несколько реплик из предыдущего батча.
Это сделано специально для контекста. Не создавай искусственных дублей.
""".strip()


def call_gemini(batch, key, batch_number, total_batches):
    transcript = "\n".join(
        row_for_prompt(row)
        for row in batch
    )

    prompt = (
        SYSTEM_RULES
        + "\n\n"
        + f"Это батч {batch_number} из {total_batches}.\n\n"
        + "ИСХОДНЫЕ РЕПЛИКИ:\n\n"
        + transcript
    )

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt,
            }]
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": RESPONSE_SCHEMA,
        },
    }

    last_error = None

    for model_number, model in enumerate(MODELS, 1):
        api = API_TEMPLATE.format(model=model)

        log()
        log(
            f"  Модель {model_number}/{len(MODELS)}: "
            f"{model}"
        )

        request_number = 1
        retry_429_used = False
        attempts_503 = 0
        max_503_attempts = 3

        while True:
            log(
                f"  Gemini: батч {batch_number}/{total_batches}, "
                f"модель {model}, запрос {request_number}"
            )

            p = subprocess.run(
                [
                    "curl",
                        "-4",
                    "-sS",
                    "--connect-timeout", "15",
                    "--max-time", "300",
                    "-X", "POST",
                    api,
                    "-H", f"x-goog-api-key: {key}",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                ],
                capture_output=True,
                text=True,
            )

            if p.returncode != 0:
                raise RuntimeError(
                    "curl завершился с ошибкой:\n"
                    + p.stderr[:2000]
                )

            try:
                response = json.loads(p.stdout)
            except Exception:
                raise RuntimeError(
                    "Gemini вернул некорректный HTTP JSON:\n"
                    + p.stdout[:2000]
                )

            error = response.get("error")

            if error:
                last_error = error
                code = error.get("code")

                # 429 оставляем как rate-limit текущего API key.
                # Если сервер прислал RetryInfo — используем его.
                if code == 429:
                    if retry_429_used:
                        if model_number < len(MODELS):
                            next_model = MODELS[model_number]
                            log(
                                f"  {model}: повторный 429. "
                                "Больше не жду."
                            )
                            log(
                                "  Переключаюсь на fallback: "
                                f"{next_model}"
                            )
                            break

                        raise RuntimeError(
                            "Последняя fallback-модель повторно "
                            "вернула 429. Останавливаюсь."
                        )

                    delay = 65

                    for detail in error.get("details", []):
                        if detail.get(
                            "@type", ""
                        ).endswith("RetryInfo"):
                            raw = detail.get(
                                "retryDelay",
                                "",
                            )

                            try:
                                delay = (
                                    int(
                                        float(
                                            raw.rstrip("s")
                                        )
                                    )
                                    + 5
                                )
                            except Exception:
                                pass

                    # Защита от аномально большого RetryInfo.
                    delay = min(delay, 65)

                    log(
                        f"  Rate limit (429) — "
                        f"один повтор через {delay} сек."
                    )

                    retry_429_used = True
                    time.sleep(delay)
                    request_number += 1
                    continue

                # 503 обычно означает временную перегрузку модели.
                # Дважды пробуем текущую модель, затем fallback.
                if code == 503:
                    attempts_503 += 1

                    if attempts_503 < max_503_attempts:
                        delay = 10 if attempts_503 == 1 else 30

                        log(
                            "  Модель временно перегружена "
                            f"(503). Один повтор через {delay} сек."
                        )

                        time.sleep(delay)
                        request_number += 1
                        continue

                    if model_number < len(MODELS):
                        next_model = MODELS[model_number]

                        log(
                            f"  {model}: 503 три раза. "
                            "Больше не жду."
                        )
                        log(
                            "  Переключаюсь на fallback: "
                            f"{next_model}"
                        )

                        break

                    raise RuntimeError(
                        "Все модели fallback вернули 503. "
                        "Последняя ошибка:\n"
                        + json.dumps(
                            error,
                            ensure_ascii=False,
                            indent=2,
                        )
                    )

                # Если fallback-модель не существует/недоступна
                # для этого API, покажем точную ошибку.
                raise RuntimeError(
                    f"Ошибка Gemini ({model}):\n"
                    + json.dumps(
                        error,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

            try:
                parts = (
                    response["candidates"][0]
                    ["content"]["parts"]
                )

                generated = "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict)
                ).strip()

                if not generated:
                    raise ValueError(
                        "В ответе нет текстовой части"
                    )

                result = json.loads(generated)

                if not isinstance(result, dict):
                    raise ValueError(
                        "Корень structured output не object"
                    )

                items = result.get("items")

                if not isinstance(items, list):
                    raise ValueError(
                        "В structured output отсутствует items[]"
                    )

                return items, model

            except Exception as e:
                raise RuntimeError(
                    "Не удалось разобрать structured output "
                    f"Gemini ({model}): "
                    + str(e)
                    + "\n\nОтвет:\n"
                    + json.dumps(
                        response,
                        ensure_ascii=False,
                        indent=2,
                    )[:5000]
                )

    raise RuntimeError(
        "Не удалось получить ответ ни от одной модели:\n"
        + json.dumps(
            last_error,
            ensure_ascii=False,
            indent=2,
        )
    )



SPEAKER_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {
                        "type": "string",
                    },
                    "name": {
                        "type": ["string", "null"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": [
                            "explicit",
                            "unknown",
                        ],
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
                },
                "required": [
                    "speaker",
                    "name",
                    "confidence",
                    "source_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["speakers"],
    "additionalProperties": False,
}


SPEAKER_RULES = """
Определи реальные имена участников встречи ПО ВОЗМОЖНОСТИ.

КРИТИЧЕСКОЕ ПРАВИЛО:
НИЧЕГО НЕ УГАДЫВАЙ.

Нужно сопоставить технические обозначения вида
"Спикер 0", "Спикер 1" с именами людей.

Имя можно указать ТОЛЬКО если из разговора есть достаточно
прямое основание считать, что это имя принадлежит именно
этому спикеру.

Допустимые основания:

1. ПРЯМОЕ:
- человек явно представился;
- другой участник однозначно обращается к этому человеку
  по имени;
- обращение по имени и непосредственно следующий ответ
  однозначно связывают имя со спикером.

2. ОДНОЗНАЧНАЯ ЦЕПОЧКА СВИДЕТЕЛЬСТВ:
Имя также можно определить по нескольким репликам,
если ВМЕСТЕ они однозначно устанавливают личность.

Например:

Спикер A спрашивает другого участника:
"Какие задачи закреплены за <ИМЯ_A>?"

Другой участник описывает обязанности <ИМЯ_A>.

Позже Спикер B от первого лица подробно описывает именно
свои обязанности, совпадающие с обсуждаемыми обязанностями
<ИМЯ_A>, а Спикер A обращается к нему:
"<ИМЯ_A>, подтвердите получение задачи."

Если вся цепочка разговора однозначно показывает,
что Спикер B — это <ИМЯ_A>, такое соответствие допустимо.

Для такого вывода source_ids должны содержать НЕ ОДНУ
случайную реплику, а минимальный набор реплик,
который позволяет человеку проверить всю логическую цепочку.

ВАЖНО:
это не означает, что можно угадывать имя просто по похожей
должности или теме. Цепочка должна быть однозначной именно
в контексте этой встречи.

НЕДОПУСТИМО:
- угадывать имя по полу;
- угадывать по должности без других подтверждений;
- угадывать по теме разговора;
- считать, что любое упомянутое имя принадлежит одному
  из участников;
- использовать внешние знания;
- использовать вероятностные догадки;
- выбирать наиболее вероятного человека, если остаётся
  хотя бы две разумные интерпретации.

Пример прямого допустимого вывода:

Спикер 0:
"<ИМЯ_B>, подтвердите получение задачи."

Спикер 2:
"Подтверждаю получение."

Это может подтверждать:
Спикер 2 = <ИМЯ_B>.

Пример недостаточного основания:

"Я передам информацию <ИМЯ_B> позже."

НЕ означает, что какой-либо другой спикер = <ИМЯ_B>.

Проверяй не только соседние реплики.
Перед ответом проанализируй ВСЮ встречу и попробуй найти
однозначные цепочки свидетельств для каждого технического
спикера.

Консервативность означает "не угадывать", а не
"использовать только прямое обращение".

Если доказательств недостаточно:
name = null
confidence = "unknown"
source_ids = []

Если соответствие прямо подтверждается:
confidence = "explicit"
и source_ids должны содержать реплики,
позволяющие проверить соответствие.

Нельзя ссылаться на несуществующие source_ids.

Верни только JSON заданной схемы.
""".strip()



SPEAKER_CONSOLIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_speaker": {
                        "type": "string",
                    },
                    "members": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
                    "confidence": {
                        "type": "string",
                        "enum": [
                            "explicit",
                            "strong",
                        ],
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
                    "reason": {
                        "type": "string",
                    },
                },
                "required": [
                    "canonical_speaker",
                    "members",
                    "confidence",
                    "source_ids",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["groups"],
    "additionalProperties": False,
}


SPEAKER_CONSOLIDATION_RULES = """
Ты анализируешь полную расшифровку встречи.

Из-за технической нарезки одного аудиофайла на части
один и тот же реальный человек мог получить разные
технические обозначения:

Спикер 1
Спикер 4
Спикер 8
Спикер 10

Твоя задача — определить ТОЛЬКО те технические speaker ID,
которые достаточно надёжно принадлежат одному реальному
участнику.

КРИТИЧЕСКОЕ ПРАВИЛО:
НЕ УГАДЫВАЙ.

Объединять спикеров можно только если есть сильные
контекстные доказательства по всей встрече.

Допустимые доказательства:

1. Один технический speaker ID продолжает мысль другого
после технической границы чанка таким образом, что это
явно одна непрерывная реплика.

2. Участник до и после границы говорит от первого лица
об одних и тех же своих действиях, документах,
обязанностях или намерениях, и альтернативная
интерпретация практически исключена.

3. Другие участники одинаково обращаются к двум
техническим speaker ID по одному имени.

4. Есть несколько независимых реплик, которые вместе
однозначно показывают, что два ID — один человек.

НЕДОПУСТИМО объединять только потому, что:
- совпадает пол;
- похож стиль речи;
- похожа должность;
- оба говорят на одну тему;
- один появился после исчезновения другого;
- кажется вероятным, что участников встречи было мало.

Если доказательств недостаточно — НЕ включай такие ID
в одну группу.

Группа из одного участника не нужна.

canonical_speaker:
выбери технический ID с наименьшим числом в группе.
Например для
Спикер 1, Спикер 4, Спикер 10
canonical_speaker = "Спикер 1".

confidence:
explicit — есть практически прямое доказательство;
strong — доказательство составное, но однозначное.

source_ids:
минимальный набор исходных реплик, по которым человек
может проверить вывод.

reason:
очень кратко объясни основание объединения.

Нельзя использовать внешние знания.
Нельзя ссылаться на несуществующие source_ids.

Верни только JSON заданной схемы.
""".strip()


def consolidate_speakers(rows, key):
    speakers = sorted({
        row["speaker"]
        for row in rows
        if row.get("speaker")
    })

    allowed_speakers = set(speakers)
    all_ids = {
        row["id"]
        for row in rows
    }

    # Если технический спикер один, анализ не нужен.
    if len(speakers) < 2:
        return rows, [], None

    transcript = "\\n".join(
        row_for_prompt(row)
        for row in rows
    )

    prompt = (
        SPEAKER_CONSOLIDATION_RULES
        + "\\n\\n"
        + "ТЕХНИЧЕСКИЕ СПИКЕРЫ:\\n"
        + "\\n".join(speakers)
        + "\\n\\n"
        + "ПОЛНАЯ РАСШИФРОВКА:\\n\\n"
        + transcript
    )

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt,
            }],
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": SPEAKER_CONSOLIDATION_SCHEMA,
        },
    }

    last_error = None

    for model_number, model in enumerate(MODELS, 1):
        api = API_TEMPLATE.format(model=model)

        log()
        log(
            "Нормализация спикеров: "
            f"модель {model_number}/{len(MODELS)} "
            f"{model}"
        )

        request_number = 1
        retry_429_used = False
        attempts_503 = 0
        max_503_attempts = 3

        while True:
            p = subprocess.run(
                [
                    "curl",
                        "-4",
                    "-sS",
                    "--connect-timeout", "15",
                    "--max-time", "300",
                    "-X", "POST",
                    api,
                    "-H", f"x-goog-api-key: {key}",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                ],
                capture_output=True,
                text=True,
            )

            if p.returncode != 0:
                raise RuntimeError(
                    "curl завершился с ошибкой:\\n"
                    + p.stderr[:2000]
                )

            try:
                response = json.loads(p.stdout)
            except Exception:
                raise RuntimeError(
                    "Gemini вернул некорректный HTTP JSON:\\n"
                    + p.stdout[:2000]
                )

            error = response.get("error")

            if error:
                last_error = error
                code = error.get("code")

                if code == 429:
                    if retry_429_used:
                        if model_number < len(MODELS):
                            next_model = MODELS[model_number]
                            log(
                                "Нормализация спикеров: "
                                f"{model} повторно вернула 429; "
                                f"перехожу к {next_model}."
                            )
                            break

                        raise RuntimeError(
                            "Последняя модель повторно вернула 429"
                        )

                    delay = 65

                    for detail in error.get("details", []):
                        if detail.get(
                            "@type", ""
                        ).endswith("RetryInfo"):
                            raw = detail.get(
                                "retryDelay",
                                "",
                            )
                            try:
                                delay = (
                                    int(
                                        float(
                                            raw.rstrip("s")
                                        )
                                    )
                                    + 5
                                )
                            except Exception:
                                pass

                    delay = min(delay, 65)

                    log(
                        "Нормализация спикеров: "
                        f"429, один повтор через {delay} сек."
                    )

                    retry_429_used = True
                    time.sleep(delay)
                    request_number += 1
                    continue

                if code == 503:
                    attempts_503 += 1

                    if attempts_503 < max_503_attempts:
                        delay = 10 if attempts_503 == 1 else 30
                        log(
                            "Нормализация спикеров: "
                            f"503, повтор через {delay} сек."
                        )
                        time.sleep(delay)
                        request_number += 1
                        continue

                    if model_number < len(MODELS):
                        next_model = MODELS[model_number]
                        log(
                            "Нормализация спикеров: "
                            f"{model}: 503 три раза; "
                            f"перехожу к {next_model}."
                        )
                        break

                    raise RuntimeError(
                        "Все модели вернули 503"
                    )

                raise RuntimeError(
                    f"Ошибка Gemini ({model}): "
                    + json.dumps(
                        error,
                        ensure_ascii=False,
                    )
                )

            try:
                parts = (
                    response["candidates"][0]
                    ["content"]["parts"]
                )

                generated = "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict)
                ).strip()

                result = json.loads(generated)

            except Exception as e:
                raise RuntimeError(
                    "Не удалось разобрать нормализацию: "
                    + str(e)
                )

            groups = []
            claimed = set()

            for group in result.get("groups", []):
                if not isinstance(group, dict):
                    continue

                members = [
                    str(x)
                    for x in group.get("members", [])
                    if str(x) in allowed_speakers
                ]

                # Убираем повторы, сохраняя порядок.
                members = list(dict.fromkeys(members))

                if len(members) < 2:
                    continue

                # Один speaker не может попасть в две группы.
                if any(x in claimed for x in members):
                    continue

                source_ids = [
                    str(x)
                    for x in group.get("source_ids", [])
                    if str(x) in all_ids
                ]

                confidence = group.get("confidence")

                if (
                    confidence not in {"explicit", "strong"}
                    or not source_ids
                ):
                    continue

                def speaker_number(value):
                    m = re.search(r"(\\d+)$", value)
                    return int(m.group(1)) if m else 10**9

                canonical = min(
                    members,
                    key=speaker_number,
                )

                clean_group = {
                    "canonical_speaker": canonical,
                    "members": sorted(
                        members,
                        key=speaker_number,
                    ),
                    "confidence": confidence,
                    "source_ids": sorted(
                        set(source_ids)
                    ),
                    "reason": str(
                        group.get("reason", "")
                    ).strip(),
                }

                groups.append(clean_group)
                claimed.update(members)

            alias = {}

            for group in groups:
                canonical = group["canonical_speaker"]
                for member in group["members"]:
                    alias[member] = canonical

            normalized_rows = []

            for row in rows:
                copied = dict(row)
                original = copied["speaker"]
                copied["technical_speaker"] = original
                copied["speaker"] = alias.get(
                    original,
                    original,
                )
                normalized_rows.append(copied)

            return normalized_rows, groups, model

    raise RuntimeError(
        "Не удалось нормализовать спикеров: "
        + json.dumps(
            last_error,
            ensure_ascii=False,
        )
    )


def detect_speakers(rows, key):
    speakers = sorted({
        row["speaker"]
        for row in rows
        if row.get("speaker")
    })

    allowed_speakers = set(speakers)

    all_ids = {
        row["id"]
        for row in rows
    }

    transcript = "\n".join(
        row_for_prompt(row)
        for row in rows
    )

    prompt = (
        SPEAKER_RULES
        + "\n\n"
        + "СПИСОК ТЕХНИЧЕСКИХ СПИКЕРОВ:\n"
        + "\n".join(speakers)
        + "\n\n"
        + "ПОЛНАЯ РАСШИФРОВКА:\n\n"
        + transcript
    )

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt,
            }],
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": SPEAKER_RESPONSE_SCHEMA,
        },
    }

    last_error = None

    for model_number, model in enumerate(MODELS, 1):
        api = API_TEMPLATE.format(model=model)

        log()
        log(
            "Определение имён: "
            f"модель {model_number}/{len(MODELS)} "
            f"{model}"
        )

        request_number = 1
        retry_429_used = False
        attempts_503 = 0
        max_503_attempts = 3

        while True:
            p = subprocess.run(
                [
                    "curl",
                        "-4",
                    "-sS",
                    "--connect-timeout", "15",
                    "--max-time", "300",
                    "-X", "POST",
                    api,
                    "-H", f"x-goog-api-key: {key}",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                ],
                capture_output=True,
                text=True,
            )

            if p.returncode != 0:
                raise RuntimeError(
                    "curl завершился с ошибкой:\n"
                    + p.stderr[:2000]
                )

            try:
                response = json.loads(p.stdout)
            except Exception:
                raise RuntimeError(
                    "Gemini вернул некорректный HTTP JSON:\n"
                    + p.stdout[:2000]
                )

            error = response.get("error")

            if error:
                last_error = error
                code = error.get("code")

                if code == 429:
                    if retry_429_used:
                        if model_number < len(MODELS):
                            next_model = MODELS[model_number]
                            log(
                                "Определение имён: "
                                f"{model} повторно вернула 429; "
                                f"перехожу к {next_model}."
                            )
                            break

                        raise RuntimeError(
                            "Определение имён: последняя "
                            "fallback-модель повторно вернула 429."
                        )

                    delay = 65

                    for detail in error.get("details", []):
                        if detail.get(
                            "@type", ""
                        ).endswith("RetryInfo"):
                            raw = detail.get(
                                "retryDelay",
                                "",
                            )

                            try:
                                delay = (
                                    int(
                                        float(
                                            raw.rstrip("s")
                                        )
                                    )
                                    + 5
                                )
                            except Exception:
                                pass

                    delay = min(delay, 65)

                    log(
                        "Определение имён: "
                        f"429, один повтор через {delay} сек."
                    )

                    retry_429_used = True
                    time.sleep(delay)
                    request_number += 1
                    continue

                if code == 503:
                    attempts_503 += 1

                    if attempts_503 < max_503_attempts:
                        delay = 10 if attempts_503 == 1 else 30
                        log(
                            "Определение имён: "
                            f"503, повтор через {delay} сек."
                        )

                        time.sleep(delay)
                        request_number += 1
                        continue

                    if model_number < len(MODELS):
                        next_model = MODELS[model_number]
                        log(
                            "Определение имён: "
                            f"{model}: 503 три раза; "
                            f"перехожу к {next_model}."
                        )
                        break

                    raise RuntimeError(
                        "Все модели вернули 503"
                    )

                raise RuntimeError(
                    f"Ошибка Gemini ({model}): "
                    + json.dumps(
                        error,
                        ensure_ascii=False,
                    )
                )

            try:
                parts = (
                    response["candidates"][0]
                    ["content"]["parts"]
                )

                generated = "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict)
                ).strip()

                result = json.loads(generated)

                returned = {
                    item.get("speaker"): item
                    for item in result.get(
                        "speakers",
                        [],
                    )
                    if (
                        isinstance(item, dict)
                        and item.get("speaker")
                        in allowed_speakers
                    )
                }

                clean = []

                for speaker in speakers:
                    item = returned.get(
                        speaker,
                        {},
                    )

                    name = item.get("name")
                    confidence = item.get(
                        "confidence",
                        "unknown",
                    )

                    source_ids = [
                        str(x)
                        for x in item.get(
                            "source_ids",
                            [],
                        )
                        if str(x) in all_ids
                    ]

                    if (
                        confidence != "explicit"
                        or not isinstance(name, str)
                        or not name.strip()
                        or not source_ids
                    ):
                        name = None
                        confidence = "unknown"
                        source_ids = []
                    else:
                        name = name.strip()

                    clean.append({
                        "speaker": speaker,
                        "name": name,
                        "confidence": confidence,
                        "source_ids": sorted(
                            set(source_ids)
                        ),
                    })

                return clean, model

            except Exception as e:
                raise RuntimeError(
                    "Не удалось разобрать результат "
                    "определения имён: "
                    + str(e)
                )

    raise RuntimeError(
        "Не удалось определить имена: "
        + json.dumps(
            last_error,
            ensure_ascii=False,
        )
    )



def clean_item(item, allowed_ids):
    if not isinstance(item, dict):
        return None

    category = item.get("category")
    text = item.get("text")

    if category not in CATEGORIES:
        return None

    if not isinstance(text, str) or not text.strip():
        return None

    source_ids = item.get("source_ids")

    if not isinstance(source_ids, list):
        return None

    source_ids = [
        str(x)
        for x in source_ids
        if str(x) in allowed_ids
    ]

    # Никаких "выводов без источника".
    if not source_ids:
        return None

    responsible = item.get("responsible")
    deadline = item.get("deadline")

    if not isinstance(responsible, str):
        responsible = None
    else:
        responsible = responsible.strip() or None

    if not isinstance(deadline, str):
        deadline = None
    else:
        deadline = deadline.strip() or None

    return {
        "category": category,
        "text": text.strip(),
        "responsible": responsible,
        "deadline": deadline,
        "source_ids": sorted(set(source_ids)),
    }


def deduplicate(items):
    result = []
    seen = set()

    for item in items:
        # Точные смысловые дубли от overlap.
        # source_ids намеренно входят в ключ:
        # разные основания не склеиваем автоматически.
        key = (
            item["category"],
            item["text"].strip().casefold(),
            (item["responsible"] or "").strip().casefold(),
            (item["deadline"] or "").strip().casefold(),
            tuple(item["source_ids"]),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def sort_items(items, row_index):
    def key(item):
        indexes = [
            row_index[x]
            for x in item["source_ids"]
            if x in row_index
        ]

        first = min(indexes) if indexes else 10**12

        return (
            first,
            CATEGORIES.index(item["category"]),
            item["text"],
        )

    return sorted(items, key=key)


def build_summary(items):
    mapping = {
        "Решения": {"Решение"},
        "Договорённости": {"Договорились"},
        "Поручения": {"Поручение"},
        "Следующие шаги": {"Следующий шаг"},
        "Открытые вопросы": {"Вопрос"},
        "Риски / ограничения": {"Риск"},
        "Обсуждено без решения": {
            "Обсуждено без решения"
        },
    }

    summary = {}

    for title, categories in mapping.items():
        summary[title] = [
            item
            for item in items
            if item["category"] in categories
        ]

    return summary


def format_item_plain(item):
    parts = [
        item["category"].upper(),
        item["text"],
    ]

    if item.get("responsible"):
        parts.append(
            "Ответственный: " + item["responsible"]
        )

    if item.get("deadline"):
        parts.append(
            "Срок: " + item["deadline"]
        )

    return "\n".join(parts)



def make_html(
    transcript_path,
    rows,
    items,
    summary,
    speaker_mapping,
):
    def esc(value):
        return html.escape(
            str(value),
            quote=True,
        )

    category_classes = {
        "Решение": "decision",
        "Договорились": "agreement",
        "Поручение": "task",
        "Зафиксировали": "fixed",
        "Срок": "deadline",
        "Риск": "risk",
        "Вопрос": "question",
        "Следующий шаг": "next-step",
        "Обсуждено без решения": "discussion",
    }

    row_index = {
        row["id"]: i
        for i, row in enumerate(rows)
    }

    name_by_speaker = {
        item["speaker"]: item["name"]
        for item in speaker_mapping
        if (
            item.get("confidence") == "explicit"
            and item.get("name")
        )
    }

    def display_text(value):
        """
        Заменяет технические обозначения спикеров
        подтверждёнными именами ТОЛЬКО для HTML.

        Исходные items в JSON остаются неизменными.
        """
        text = str(value)

        # Сначала более длинные обозначения, чтобы
        # потенциальные имена не пересекались.
        replacements = sorted(
            name_by_speaker.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )

        for technical, real_name in replacements:
            text = re.sub(
                r"(?<![А-Яа-яA-Za-z0-9_])"
                + re.escape(technical)
                + r"(?![0-9])",
                real_name,
                text,
            )

        return text

    def speaker_html(speaker):
        name = name_by_speaker.get(speaker)

        if not name:
            return (
                f'<div class="speaker">'
                f'{esc(speaker)}'
                '</div>'
            )

        return (
            '<div class="speaker-name">'
            f'{esc(name)}'
            '</div>'
            '<div class="speaker-tech">'
            f'{esc(speaker)}'
            '</div>'
        )

    numbered_items = []

    for number, item in enumerate(items, 1):
        valid_ids = [
            source_id
            for source_id in item["source_ids"]
            if source_id in row_index
        ]

        valid_ids.sort(
            key=lambda x: row_index[x]
        )

        if not valid_ids:
            continue

        numbered_items.append({
            "number": number,
            "item": item,
            "source_ids": valid_ids,
            "primary_id": valid_ids[0],
        })

    primary_results = {}
    source_links = {}

    for entry in numbered_items:
        primary_results.setdefault(
            entry["primary_id"],
            [],
        ).append(entry)

        for source_id in entry["source_ids"]:
            source_links.setdefault(
                source_id,
                [],
            ).append(entry)

    def item_html(entry, compact=False):
        item = entry["item"]
        category = item["category"]

        css_class = category_classes.get(
            category,
            "other",
        )

        meta = []

        if item.get("responsible"):
            responsible = display_text(
                item["responsible"]
            )

            meta.append(
                "<div><b>Ответственный:</b> "
                + esc(responsible)
                + "</div>"
            )

        if item.get("deadline"):
            meta.append(
                "<div><b>Срок:</b> "
                + esc(item["deadline"])
                + "</div>"
            )

        sources = ", ".join(
            entry["source_ids"]
        )

        cls = (
            "summary-item"
            if compact
            else "result-item"
        )

        return (
            f'<div class="{cls}">'
            f'<div class="badge badge-{css_class}">'
            f'{esc(category)}'
            '</div>'
            f'<div class="result-text">'
            f'{esc(display_text(item["text"]))}'
            '</div>'
            + "".join(meta)
            + '<div class="sources">'
            + f'Пункт {entry["number"]} · '
            + 'источники: '
            + esc(sources)
            + "</div>"
            + "</div>"
        )

    # --------------------------------------------------------
    # Верхняя сводка
    # --------------------------------------------------------

    entries_by_item_id = {
        id(entry["item"]): entry
        for entry in numbered_items
    }

    summary_sections = []

    for title, section_items in summary.items():
        if section_items:
            rendered_items = []

            for item in section_items:
                entry = entries_by_item_id.get(
                    id(item)
                )

                if entry:
                    rendered_items.append(
                        item_html(
                            entry,
                            compact=True,
                        )
                    )

            body = "".join(rendered_items)

            if not body:
                body = (
                    '<div class="empty">'
                    'Нет зафиксированных пунктов'
                    '</div>'
                )
        else:
            body = (
                '<div class="empty">'
                'Нет зафиксированных пунктов'
                '</div>'
            )

        summary_sections.append(
            '<section class="summary-section">'
            f'<h2>{esc(title)}</h2>'
            f'{body}'
            '</section>'
        )

    # --------------------------------------------------------
    # Полная таблица.
    # ВСЕ rows отображаются всегда.
    # --------------------------------------------------------

    table_rows = []

    for row in rows:
        row_id = row["id"]

        results_here = primary_results.get(
            row_id,
            [],
        )

        links_here = source_links.get(
            row_id,
            [],
        )

        if results_here:
            result_html = "".join(
                item_html(entry)
                for entry in results_here
            )
            row_class = "has-result"

        elif links_here:
            refs = ", ".join(
                f'пункт {entry["number"]}'
                for entry in links_here
            )

            result_html = (
                '<div class="source-reference">'
                '↳ Также источник: '
                + esc(refs)
                + '</div>'
            )

            row_class = "source-only"

        else:
            result_html = ""
            row_class = ""

        table_rows.append(
            f'<tr class="{row_class}">'
            '<td class="who">'
            f'<div class="time">{esc(row["start"])}</div>'
            f'{speaker_html(row["speaker"])}'
            f'<div class="source-id">{esc(row_id)}</div>'
            '</td>'
            '<td class="original">'
            f'{esc(row["text"])}'
            '</td>'
            '<td class="result">'
            f'{result_html}'
            '</td>'
            '</tr>'
        )

    title = transcript_path.stem.replace(
        "_ПОЛНАЯ_РАСШИФРОВКА",
        "",
    )

    identified = [
        (
            item["speaker"],
            item["name"],
        )
        for item in speaker_mapping
        if (
            item.get("confidence") == "explicit"
            and item.get("name")
        )
    ]

    if identified:
        speaker_note = " · ".join(
            f"{name} ({speaker})"
            for speaker, name in identified
        )
    else:
        speaker_note = (
            "имена участников не определены"
        )

    return """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>Протокол — __TITLE__</title>

<style>
:root {
    --bg: #f5f6f8;
    --card: #ffffff;
    --text: #202124;
    --muted: #6b7280;
    --line: #e5e7eb;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    line-height: 1.45;
}

.container {
    max-width: 1600px;
    margin: 0 auto;
    padding: 28px;
}

.header {
    margin-bottom: 20px;
}

h1 {
    margin: 0 0 6px;
    font-size: 29px;
}

.subtitle {
    color: var(--muted);
}

.speaker-note {
    margin-top: 5px;
    color: var(--muted);
    font-size: 13px;
}

.summary-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
    margin: 22px 0 28px;
}

.summary-section {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px;
}

.summary-section h2 {
    font-size: 17px;
    margin: 0 0 12px;
}

.summary-item {
    padding: 11px 0;
    border-top: 1px solid var(--line);
}

.summary-item:first-of-type {
    border-top: 0;
}

.table-wrap {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
}

th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: #f9fafb;
    text-align: left;
    font-size: 13px;
    padding: 12px;
    border-bottom: 1px solid var(--line);
}

td {
    vertical-align: top;
    padding: 13px 12px;
    border-bottom: 1px solid var(--line);
}

th:nth-child(1),
td:nth-child(1) {
    width: 145px;
}

th:nth-child(2),
td:nth-child(2) {
    width: 52%;
}

.original {
    white-space: pre-wrap;
}

.time {
    font-variant-numeric: tabular-nums;
    font-weight: 700;
}

.speaker {
    margin-top: 4px;
}

.speaker-name {
    margin-top: 4px;
    font-weight: 700;
}

.speaker-tech {
    color: var(--muted);
    font-size: 11px;
    margin-top: 1px;
}

.source-id,
.sources {
    color: var(--muted);
    font-size: 11px;
    margin-top: 5px;
}

.result-item {
    margin-bottom: 14px;
}

.result-item:last-child {
    margin-bottom: 0;
}

.result-text {
    font-weight: 600;
    margin: 5px 0 4px;
}

.source-reference {
    color: var(--muted);
    font-size: 12px;
}

.has-result {
    background: #fcfdff;
}

.source-only {
    background: #fdfdfe;
}

.empty {
    color: var(--muted);
}

.badge {
    display: inline-block;
    padding: 4px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
}

.badge-decision {
    background: #dcfce7;
    color: #166534;
}

.badge-agreement {
    background: #dbeafe;
    color: #1d4ed8;
}

.badge-task {
    background: #fef3c7;
    color: #92400e;
}

.badge-fixed {
    background: #e0e7ff;
    color: #4338ca;
}

.badge-deadline {
    background: #ffedd5;
    color: #9a3412;
}

.badge-risk {
    background: #fee2e2;
    color: #b91c1c;
}

.badge-question {
    background: #f3e8ff;
    color: #7e22ce;
}

.badge-next-step {
    background: #ccfbf1;
    color: #0f766e;
}

.badge-discussion,
.badge-other {
    background: #f3f4f6;
    color: #4b5563;
}

@media (max-width: 850px) {
    .container {
        padding: 12px;
    }

    table {
        min-width: 1000px;
    }
}

@media print {
    body {
        background: white;
    }

    .container {
        max-width: none;
        padding: 0;
    }
}
</style>
</head>

<body>
<div class="container">

<div class="header">
    <h1>Протокол встречи</h1>

    <div class="subtitle">
        __TITLE__ ·
        __ROWS__ реплик ·
        __ITEMS__ пунктов протокола
    </div>

    <div class="speaker-note">
        __SPEAKERS__
    </div>
</div>

<div class="summary-grid">
__SUMMARY__
</div>

<div class="table-wrap">
<table>

<thead>
<tr>
    <th>Время / кто</th>
    <th>Полная исходная расшифровка</th>
    <th>Зафиксированный результат</th>
</tr>
</thead>

<tbody>
__TABLE__
</tbody>

</table>
</div>

</div>
</body>
</html>
""".replace(
        "__TITLE__",
        esc(title),
    ).replace(
        "__ROWS__",
        str(len(rows)),
    ).replace(
        "__ITEMS__",
        str(len(items)),
    ).replace(
        "__SPEAKERS__",
        esc(speaker_note),
    ).replace(
        "__SUMMARY__",
        "\n".join(summary_sections),
    ).replace(
        "__TABLE__",
        "\n".join(table_rows),
    )



def output_paths(transcript_path):
    suffix = "_ПОЛНАЯ_РАСШИФРОВКА"

    stem = transcript_path.stem

    if stem.endswith(suffix):
        base = stem[:-len(suffix)]
    else:
        base = stem

    json_path = transcript_path.with_name(
        base + "_ПРОТОКОЛ.json"
    )

    html_path = transcript_path.with_name(
        base + "_ПРОТОКОЛ.html"
    )

    return json_path, html_path


def main():
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Использование:\n"
            "gemini_make_protocol.py "
            "\"..._ПОЛНАЯ_РАСШИФРОВКА.txt\""
        )

    transcript_path = (
        Path(sys.argv[1])
        .expanduser()
        .resolve()
    )

    if not transcript_path.is_file():
        raise RuntimeError(
            f"Файл не найден: {transcript_path}"
        )

    log("=" * 60)
    log("GEMINI MEETING PROTOCOL")
    log(f"Расшифровка: {transcript_path}")

    rows = parse_transcript(transcript_path)
    batches = make_batches(rows)

    log(f"Реплик: {len(rows)}")
    log(f"Текстовых батчей: {len(batches)}")
    log(
        f"Overlap между батчами: "
        f"{OVERLAP_UTTERANCES} реплик"
    )
    log("=" * 60)

    key = get_key()

    # Сначала объединяем технические speaker ID, которые
    # появились из-за независимой diarization аудиочанков.
    # Ошибка этого дополнительного этапа не должна ломать
    # создание протокола.
    original_rows = rows
    speaker_groups = []
    speaker_normalization_model = None

    try:
        normalized_rows, speaker_groups, speaker_normalization_model = (
            consolidate_speakers(
                rows,
                key,
            )
        )

        rows = normalized_rows
        batches = make_batches(rows)

        log()
        log("Нормализация спикеров завершена:")

        if speaker_groups:
            for group in speaker_groups:
                log(
                    "  "
                    + " = ".join(group["members"])
                    + " -> "
                    + group["canonical_speaker"]
                    + " ["
                    + group["confidence"]
                    + "]"
                )
        else:
            log(
                "  Надёжных объединений технических "
                "спикеров не найдено."
            )

    except Exception as e:
        log()
        log(
            "ПРЕДУПРЕЖДЕНИЕ: "
            "не удалось нормализовать спикеров."
        )
        log(str(e))
        log(
            "Продолжаю с исходными техническими "
            "speaker ID."
        )

        rows = original_rows
        batches = make_batches(rows)

    # Определяем реальные имена уже ПОСЛЕ глобальной
    # нормализации speaker ID.
    try:
        speaker_mapping, speaker_model = detect_speakers(
            rows,
            key,
        )

        log()
        log("Определение имён завершено:")

        for speaker_info in speaker_mapping:
            if speaker_info["name"]:
                log(
                    "  "
                    + speaker_info["speaker"]
                    + " -> "
                    + speaker_info["name"]
                    + " ["
                    + ", ".join(
                        speaker_info["source_ids"]
                    )
                    + "]"
                )
            else:
                log(
                    "  "
                    + speaker_info["speaker"]
                    + " -> неизвестно"
                )

    except Exception as e:
        log()
        log(
            "ПРЕДУПРЕЖДЕНИЕ: "
            "не удалось определить имена."
        )
        log(str(e))

        speaker_mapping = [
            {
                "speaker": speaker,
                "name": None,
                "confidence": "unknown",
                "source_ids": [],
            }
            for speaker in sorted({
                row["speaker"]
                for row in rows
            })
        ]

        speaker_model = None

    all_ids = {
        row["id"]
        for row in rows
    }

    all_items = []
    used_models = set()

    for number, batch in enumerate(
        batches,
        1,
    ):
        batch_ids = {
            row["id"]
            for row in batch
        }

        raw_items, used_model = call_gemini(
            batch,
            key,
            number,
            len(batches),
        )

        used_models.add(used_model)

        accepted = 0

        for raw_item in raw_items:
            item = clean_item(
                raw_item,
                batch_ids,
            )

            if item is None:
                continue

            # Дополнительная локальная проверка:
            # source_ids обязаны существовать
            # в полной расшифровке.
            if not all(
                source_id in all_ids
                for source_id in item["source_ids"]
            ):
                continue

            all_items.append(item)
            accepted += 1

        log(
            f"  Принято пунктов из батча: {accepted}"
        )

    before = len(all_items)

    all_items = deduplicate(all_items)

    row_index = {
        row["id"]: i
        for i, row in enumerate(rows)
    }

    all_items = sort_items(
        all_items,
        row_index,
    )

    removed = before - len(all_items)

    summary = build_summary(all_items)

    json_path, html_path = output_paths(
        transcript_path
    )

    service_dir = transcript_path.parent / "_service"
    service_dir.mkdir(exist_ok=True)

    speaker_normalization_path = (
        service_dir / "speaker_normalization.json"
    )

    with speaker_normalization_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            {
                "model": speaker_normalization_model,
                "groups": speaker_groups,
                "original_speakers": sorted({
                    row["speaker"]
                    for row in original_rows
                }),
                "normalized_speakers": sorted({
                    row["speaker"]
                    for row in rows
                }),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    result = {
        "source_transcript": str(transcript_path),
        "models_used": sorted(used_models),
        "models_configured": MODELS,
        "rows_count": len(rows),
        "batch_count": len(batches),
        "batch_overlap_utterances": OVERLAP_UTTERANCES,
        "items_count": len(all_items),
        "exact_duplicates_removed": removed,
        "categories": CATEGORIES,
        "speaker_groups": speaker_groups,
        "speaker_normalization_model": speaker_normalization_model,
        "speaker_mapping": speaker_mapping,
        "speaker_detection_model": speaker_model,
        "summary": summary,
        "items": all_items,
        "transcript": rows,
    }

    with json_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2,
        )

    rendered = make_html(
        transcript_path,
        rows,
        all_items,
        summary,
        speaker_mapping,
    )

    html_path.write_text(
        rendered,
        encoding="utf-8",
    )

    log()
    log("=" * 60)
    log("ПРОТОКОЛ ГОТОВ")
    log(f"Пунктов: {len(all_items)}")
    log(f"Точных дублей удалено: {removed}")
    log(f"JSON: {json_path}")
    log(f"HTML: {html_path}")
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print()
        print(
            "ОШИБКА ПРОТОКОЛА:",
            e,
            file=sys.stderr,
        )
        sys.exit(1)
