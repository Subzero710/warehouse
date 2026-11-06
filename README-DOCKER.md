# Utilisation 100 % Docker Compose

Prérequis : Docker Engine avec le plugin Compose.

Le service `mongodb` utilise MongoDB 8.2 et un volume persistant nommé
`warehouse-mongo-data`. Le service `app` utilise Python 3.11 et installe
automatiquement Flask, PyMongo, pytest et ruff à la construction.

## Démarrer l'API et MongoDB

```bash
docker compose up -d --build
docker compose ps
```

API : http://localhost:5000

```bash
curl http://localhost:5000/meta
```

La réponse doit indiquer `"backend": "mongo"`.

## Insérer 20 000 produits

```bash
docker compose exec app python seed.py --count 20000
```

Cette commande AJOUTE des produits. Pour effacer la base `warehouse`
avant l'insertion, `python seed.py --reset --count 20000` existe,
mais cette commande détruit toutes les données de cette base.

## Tests

```bash
docker compose exec app pytest -xvv
```

Les tests MongoDB utilisent `warehouse_test` (séparée de `warehouse`).
Le paramètre `MONGO_TEST_URI` est fourni par Compose.

## Benchmark

```bash
docker compose exec app python bench.py --url http://localhost:5000 --runs 20
```

`localhost` désigne ici le conteneur `app`, dans lequel l'API tourne.

## Ouvrir mongosh

```bash
docker compose exec mongodb mongosh
```

## Développement

Le dossier local est monté dans `/app` : les modifications Python
dans PyCharm sont visibles dans le conteneur. Si vous changez
`requirements.txt` ou `requirements-dev.txt`, reconstruisez l'image :

```bash
docker compose up -d --build
```

Arrêter les conteneurs sans supprimer les données :

```bash
docker compose down
```

Attention : `docker compose down -v` efface le volume de données.

Les ports MongoDB et Flask sont exposés seulement sur l'interface
localhost de la VM. Le programme Flask reste en mode debug (voir `run.py`),
donc ne le publiez pas sur Internet.
