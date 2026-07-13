# SQL Query Optimizer

Sistem parsira SQL upit, vrši sintaksnu i semantičku provjeru, bira optimalni plan evaluacije i procjenjuje cijenu svakog koraka u **broju blok transfera**.

---

## Sadržaj

- [Pokretanje](#pokretanje)
- [Format ulaznih podataka](#format-ulaznih-podataka)
- [Podržane operacije](#podržane-operacije)
- [Arhitektura](#arhitektura)
- [Model troškova](#model-troškova)
- [Primjeri](#primjeri)

---

## Pokretanje

### Zahtjevi

- Python 3.10+
- Nema eksternih zavisnosti (koristi samo standardnu biblioteku)

### Komandna linija

```bash
python main.py --schema <putanja_do_sheme.json> --buffer <broj_blokova> --query "<SQL upit>"
```

**Argumenti:**

| Argument | Opis | Podrazumijevano |
|----------|------|----------------|
| `--schema` | Putanja do JSON fajla sa shemom baze | obavezno |
| `--buffer` | Veličina bafera u blokovima (B) | `10` |
| `--query`  | SQL upit kao string (opcionalno — ako se izostavi, unosi se interaktivno) | — |

### Primjer

```bash
python main.py \
  --schema data/example_schema.json \
  --buffer 10 \
  --query "SELECT Students.name, Courses.title
           FROM Students, Enrollments, Courses
           WHERE Students.id = Enrollments.student_id
             AND Enrollments.course_id = Courses.id
             AND Students.dept_id = 5"
```

---

## Format ulaznih podataka

### Shema baze (JSON)

```json
{
  "tables": [
    {
      "name": "Students",
      "attributes": [
        { "name": "id",      "type": "int",         "is_unique": true,  "n_distinct": 10000 },
        { "name": "name",    "type": "varchar(50)",  "is_unique": false, "n_distinct": 9500  },
        { "name": "dept_id", "type": "int",          "is_unique": false, "n_distinct": 20    }
      ],
      "n_rows": 10000,
      "n_blocks": 200,
      "rows_per_block": 50,
      "indexes": [
        { "attributes": ["id"],      "type": "btree", "is_clustering": true,  "height": 3 },
        { "attributes": ["dept_id"], "type": "hash",  "is_clustering": false, "height": null }
      ]
    }
  ]
}
```

**Polja sheme:**

| Polje | Opis |
|-------|------|
| `name` | Naziv tabele |
| `attributes[].type` | Tip atributa: `int`, `float`, `varchar(n)`, `date`, `bool` |
| `attributes[].is_unique` | Da li atribut ima jedinstvene vrijednosti |
| `attributes[].n_distinct` | Broj različitih vrijednosti — V(A, r) |
| `n_rows` | Ukupan broj redova — nr |
| `n_blocks` | Broj blokova na disku — br |
| `rows_per_block` | Faktor blokiranja — fr |
| `indexes[].type` | `btree` ili `hash` |
| `indexes[].is_clustering` | Da li je sortirajući/grupišući indeks |
| `indexes[].height` | Visina B+ stabla (null za hash indeks) |

### SQL upit

Podržana sintaksa:

```sql
SELECT attr1, attr2, ...
FROM tabela1, tabela2, ...
WHERE uslov1 AND uslov2 AND ...
ORDER BY atribut
```

**Ograničenja (prema postavci projekta):**
- Maksimalno **4 tabele** u FROM klauzuli
- Maksimalno **6 uslova** u WHERE klauzuli
- ORDER BY maksimalno **1 atribut**
- Bez podupita, bez GROUP BY, bez agregatnih funkcija
- WHERE sadrži isključivo konjunkciju uslova (AND)
- Operatori: `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE`

---

## Podržane operacije

### Selekcija

| Algoritam | Kada se koristi |
|-----------|----------------|
| B+ stablo (sortirajući), tačno poklapanje | B+ clustering indeks na atributu jednakosti |
| Heš indeks, tačno poklapanje | Heš indeks na atributu jednakosti |
| B+ stablo (nesortirajući), tačno poklapanje | B+ non-clustering indeks na atributu jednakosti |
| B+ stablo, opsežna pretraga | B+ indeks na atributu poređenja |
| Linearno skeniranje | Nema indeksa ili disjunktivni uslov |

### Spajanje (Join)

| Algoritam | Cijena |
|-----------|--------|
| Ugniježdena petlja (NL) | br · bs |
| Blok ugniježdena petlja (BNL) | br + ⌈br/(B−2)⌉ · bs |
| Indeksirana ugniježdena petlja (INL) | br + nr · cijena_probe |
| Sort-merge join | br + bs + sort troškovi |
| Hash join | 3 · (br + bs) |

Redosljed spajanja određuje se **greedy heuristikom**: u svakom koraku spaja se par čiji je međurezultat najmanji.

### Sortiranje

Vanjsko sort-merge sortiranje:
```
Cijena = 2 · br · (1 + ⌈log_{B−1}(⌈br/B⌉)⌉)
```

### Projekcija

Eliminacija kolona bez uklanjanja duplikata — **protočna (pipelined) operacija, cijena 0** (BP2-6, str. 33–34).

### ORDER BY

Ako postoji sortirajući (clustering) indeks na atributu — nulta cijena.  
U suprotnom — vanjsko sort-merge sortiranje.

---

## Arhitektura

```
sql_optimizer/
├── models/
│   ├── schema.py               # Table, Attribute, Index, IntermediateResult
│   ├── query.py                # ParsedQuery, Condition
│   └── plan.py                 # OperationStep, EvaluationPlan
│
├── parser/
│   ├── schema_loader.py        # JSON → Schema objekti
│   └── sql_parser.py           # SQL string → ParsedQuery
│
├── validator/
│   └── semantic_checker.py     # Provjera tabela, atributa, ambiguiteta
│
├── optimizer/
│   ├── selectivity_estimator.py   # Procjena kardinalnosti (V(A,r), selektivnost)
│   ├── cost_estimator.py          # I/O cost formule (blok transferi)
│   ├── selection_optimizer.py     # Odabir algoritma selekcije
│   ├── join_optimizer.py          # Odabir join algoritma + greedy redosljed
│   └── plan_builder.py            # Sastavljanje kompletnog plana evaluacije
│
└── output/
    └── plan_printer.py         # Formatiran ispis plana
```

**Tok izvršavanja:**

```
JSON shema ──► schema_loader ──► Schema
SQL string ──► sql_parser    ──► ParsedQuery
                                     │
                              semantic_checker
                                     │
                              plan_builder
                             ┌───────┴────────┐
                    selection_optimizer   join_optimizer
                             │                │
                    cost_estimator   selectivity_estimator
                             │
                        EvaluationPlan
                             │
                        plan_printer ──► stdout
```

---

## Model troškova

Sve pretpostavke sistema:

| Pretpostavka | Obrazloženje |
|-------------|-------------|
| Uniformna distribucija vrijednosti | Nema histograma; selektivnost = 1/V(A,r) |
| Materijalizacija međurezultata | Svaki korak plana upisuje rezultat na disk |
| Opsežne pretrage: selektivnost n_r/2 | Bez min/max i histograma (BP2-6, str. 25) |
| Blok = 4096 bajtova | Za procjenu faktora blokiranja međurezultata |
| Heš indeks bez prekoračenja | Cijena pristupa = 1 blok transfer |
| Projekcija bez eliminacije duplikata | Samo eliminacija kolona |

---

## Primjeri

### Upit nad jednom tabelom

```bash
python main.py --schema data/example_schema.json --buffer 5 \
  --query "SELECT Students.name, Students.gpa FROM Students
           WHERE Students.id = 42 AND Students.gpa > 3.5
           ORDER BY Students.gpa"
```

### Upit nad dvije tabele

```bash
python main.py --schema data/example_schema.json --buffer 10 \
  --query "SELECT Students.name, Enrollments.grade
           FROM Students, Enrollments
           WHERE Students.id = Enrollments.student_id
             AND Students.dept_id = 3"
```

### Upit nad tri tabele

```bash
python main.py --schema data/example_schema.json --buffer 10 \
  --query "SELECT Students.name, Courses.title
           FROM Students, Enrollments, Courses
           WHERE Students.id = Enrollments.student_id
             AND Enrollments.course_id = Courses.id
             AND Students.dept_id = 5"
```
