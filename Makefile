.PHONY: install seed test eval serve ui docker

install:
	pip install -e ".[all,dev]"

seed:
	t2sql seed

test:
	pytest -q

eval: seed
	t2sql eval --report reports

serve:
	t2sql serve --port 8000

ui:
	streamlit run src/t2sql/ui/app.py

docker:
	docker compose up --build
