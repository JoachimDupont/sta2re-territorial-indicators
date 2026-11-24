# STA²RE — moteur d'indicateurs territoriaux et environnementaux

Prototype open-data du projet INTERREG STA²RE. Le dépôt accueille les connecteurs, traitements géomatiques, définitions d'indicateurs et exports réutilisables par les partenaires.

## Pilote 01 — îlots de chaleur urbains (ICU)

Territoire : communes wallonnes partageant une frontière terrestre avec la France.

Croisement prévu :
- cartographie AWAC des ICU (couche raster 12, EPSG:31370) ;
- population Statbel 2026 par secteur statistique ;
- géométries des secteurs statistiques 2026 ;
- limites communales wallonnes.

Le millésime de population 2026 ne doit pas être confondu avec le millésime de la cartographie ICU. Le résultat sera formulé comme « population 2026 exposée selon la cartographie ICU AWAC disponible ».

### Première étape : identifier les communes frontalières

Le script télécharge les limites communales wallonnes du Géoportail et les géométries des communes françaises des départements frontaliers candidats. Il retient les communes wallonnes partageant une frontière linéaire avec la géométrie française.

Exécution locale :

```bash
pip install -r requirements.txt
python scripts/identify_border_communes.py
```

Les fichiers GeoJSON et CSV sont écrits dans data/reference/.

Une exécution à distance est aussi disponible dans GitHub Actions : onglet Actions → Identify Walloon border communes → Run workflow. Le workflow publie les deux fichiers comme artefact téléchargeable.

## Documentation

- [Méthodologie du pilote ICU](docs/methodologie_icu.md)

## État d'avancement

- [x] Dépôt et documentation initiale
- [x] Dépendances géomatiques
- [x] Script de sélection géométrique des communes frontalières
- [ ] Validation de la liste de communes obtenue
- [ ] Téléchargement et harmonisation population + secteurs statistiques 2026
- [ ] Lecture vérifiée des classes du raster ICU AWAC
- [ ] Calcul des indicateurs par secteur et agrégation communale
- [ ] Export GeoJSON/CSV et visualisation web

## Sources principales

- [Géoportail de la Wallonie — limites administratives](https://geoservices.wallonie.be/arcgis/rest/services/LIMITES/LIMITES_ADMINISTRATIVES/MapServer/3)
- [AWAC — couche ICU](https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/12)
- [Statbel — open data 2026](https://statbel.fgov.be/fr/open-data?category=209)
- [API officielle des communes françaises](https://geo.api.gouv.fr/decoupage-administratif/communes)
