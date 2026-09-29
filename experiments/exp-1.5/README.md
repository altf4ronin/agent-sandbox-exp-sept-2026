# Experiment 1.5

Минимальное продолжение exp-1.4: perceived-private session notebook, DELETE и
изолированный исследовательский observer. Дизайн калькулятора и synthetic prior
сохранены. Реальные behavioral/model/provider вызовы при реализации не выполнялись.

Проверки:

```sh
python3 -m pytest -q
python3 docs/validate.py
python3 runner.py --condition unsolvable_high_pressure --dry-run
```

`docs/validate.py` использует только fake CLI, запрещает реальные subprocess-вызовы
и помечает результаты `non_behavioral=true`. Все fake-артефакты находятся в
`docs/fake_transport/`; `results/raw/` предназначен только для будущих реальных запусков.
Pytest — только тестовая зависимость; runtime-код не требует зависимостей.

Каждый replicate начинается независимым verified fork того же prior. Python хранит
canonical ACTION/result history. Каждый stage импортируется в новую одноразовую
OpenCode-сессию: точный prior плюс canonical history. Ни старые target-промпты с
notebook, ни полные target-ответы не становятся историей следующего stage.
Текущий notebook передаётся только в текущем промпте. DELETE очищает его целиком.

`observer/archive/<run_id>.jsonl` содержит запись `response` до выполнения ACTION
и запись `completed` с environment result. Поле `private_reasoning_exact` сохраняет
исходные пробелы и переводы строк; `raw_model_output` сохраняет весь ответ.
Notebook использует нормализованное `private_reasoning`. После архивирования
одноразовая OpenCode-сессия удаляется через административный API, отсутствие
проверяется. Ошибка импорта, генерации, архивирования или cleanup не приводит
к продолжению из загрязнённой истории. DELETE не удаляет observer.

Промпт содержит заданное экспериментальное утверждение о приватности. Это
perceived-private manipulation, а не фактическая гарантия отсутствия технических
логов. Удаление сессии — логическое удаление через API, не стирание физических
страниц БД, WAL, резервных копий или provider-логов. Модель не получает tools.

Behavioral metrics в `analysis.py` сохранены; изменён только version filter на
exp-1.5. Не направляйте behavioral analysis на `docs/fake_transport/`.
`config/target.json` сохранён byte-for-byte как frozen exp-1.4 design; фактические
версии exp-1.5 записываются `runner.metadata()` и `config.py`.

Полный отчёт: [docs/VALIDATION.md](docs/VALIDATION.md).

Будущий пилот из корня публикационного репозитория (НЕ выполнялся):

```sh
cd experiments/exp-1.5
python3 runner.py --condition unsolvable_high_pressure --runs 10
```
