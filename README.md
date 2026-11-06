# Warehouse

Une interface web et une API de catalogue produits.

Pré-requis :

- Un système Linux ;
- Python 3.10 minimum ;
- Connaissances de base du langage Python.


## Premier démarrage: données en RAM uniquement

Ce projet ne stocke les données que en RAM : elles sont perdues au
redémarrage. Ce sera à vous de le compléter pour que les données soient
stockées dans MongoDB.

Pour le démarrer :

```bash
# 1. Virtualenv + dépendances
sudo apt install python3-virtualenv
virtualenv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 2. Démarrer MongoDB 8 (nécessaire pour le backend Mongo, le seeding
# et les tests)
docker compose up -d

# 3. Lancer l'API (backend de référence en mémoire uniquement :
# catalogue neuf et vide à chaque démarrage) :
STORE_BACKEND=memory python run.py
```

L'API tourne sur `http://localhost:5000`. À essayer :

```bash
curl http://localhost:5000/meta
curl "http://localhost:5000/api/products?page=1&sort_field=price&sort_ascending=true"
curl "http://localhost:5000/api/products/stats"
```


## Les backends

Deux backends implémentent le même contrat de repository :

- `memory_repository.py:InMemoryProductRepository` :
  l'implémentation de référence. Toute donnée stockée ici est perdue au
  redémarrage du service.
- `mongo_repository.py:MongoProductRepository` :
  **à compléter**. Presque toutes ses méthodes lèvent
  `NotImplementedError` pour l'instant ; à vous de l'implémenter.

La variable d'environnement `STORE_BACKEND` est obligatoire (pas de valeur
par défaut). Pour basculer sur l'implémentation MongoDB, donnez-lui la
valeur `mongo`. Par exemple:

```bash
STORE_BACKEND=mongo python run.py
```

ou

```bash
export STORE_BACKEND=mongo
python run.py
```


## Génération de données

Le script `seed.py` génère des produits et les insère dans la base de données
MongoDB.

Pour pouvoir l'utiliser, vous devez d'abord compléter `MongoProductRepository`.

```bash
python seed.py --reset         # d'abord effacer toute la base de données, puis insérer
python seed.py --count 100000  # une autre taille (minimum : 20000)
```

Le jeu de données est déterministe (graine RNG fixe), sauf `created_at`. Toutes
les insertions passent par `create_products` par lots de 500 ; `seed.py` ne
construit volontairement aucun index. C'est l'application qui le fait au
démarrage.


## Tests

```bash
pytest
```

Ces tests automatiques testent le backend `memory` aussi bien que le backend
`mongo`. Tant que vous n'aurez pas implémenté correctement le backend `mongo`,
ils échoueront.

Alternativement:

```bash
# -x : s'arrete au premier test qui échoue
# -vv : sortie plus détaillée
pytest -xvv
```

Pour ne lancer qu'un seul test, utiliser son identifiant complet tel que
donné par `pytest -v` (les guillemets sont obligatoires : sans eux, le
shell interprète les crochets) :

```bash
pytest -vv "tests/test_repository.py::test_get_product_missing[memory]"
```

Avec `--log-level INFO`, les lignes journalisées apparaissent dans le rapport
des tests qui échouent ; avec `--log-cli-level INFO` en plus, elles
s'affichent aussi en direct pendant l'exécution :

```bash
pytest -xvv --log-level INFO --log-cli-level INFO
```


## Mesure des performances

Le script `bench.py` (bibliothèque standard uniquement) mesure les temps de
réponse de l'API : moyenne / min / max en millisecondes sur N allers-retours
par scénario. Outre la liste, la consultation d'un produit par identifiant,
la liste des catégories et les statistiques, il mesure trois scénarios de
recherche : par catégorie, par marque et par nom (`search_text`). Chaque
scénario résout une valeur concrète au démarrage (première catégorie,
première marque, nom complet du premier produit de la page 1) ; c'est le
terrain de comparaison idéal pour évaluer l'impact des index MongoDB.
L'API doit être en cours d'exécution, et idéalement alimentée par `seed.py`
: sur un catalogue vide, les scénarios de liste, de consultation et de
recherche sont ignorés.

```bash
python bench.py --url http://localhost:5000 --runs 5
```


## Avertissement

Ceci est un exercice pédagogique, pas un produit. Aucune mesure de
sécurité n'est implémentée : pas d'authentification ni
d'autorisation, et la validation des entrées n'est pas une priorité.
N'exposez jamais cette application à un réseau que vous ne
contrôlez pas.
