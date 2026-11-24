# Pilote ICU — périmètre des 29 communes wallonnes STA²RE

## Objectif

Estimer, pour les 29 communes définies par le porteur du projet, la part de surface et la population exposées aux classes d'îlot de chaleur urbain (ICU) cartographiées par l'AWAC. Les résultats associent la population Statbel 2026 à la cartographie ICU AWAC disponible, mise à jour le 14/11/2024 : ils ne constituent pas une observation des ICU en 2026.

## Périmètre territorial

Le périmètre d'étude est une liste explicite de 29 communes, réparties dans trois provinces :
- Hainaut (15) : Comines-Warneton, Mouscron, Estaimpuis, Tournai, Brunehaut, Rumes, Péruwelz, Hensies, Quiévrain, Honnelles, Dour, Quévy, Erquelinnes, Momignies et Chimay.
- Namur (7) : Couvin, Viroinval, Doische, Hastière, Beauraing, Gedinne et Vresse-sur-Semois.
- Luxembourg (7) : Aubange, Arlon, Musson, Virton, Rouvroy, Florenville et Bouillon.

Cette liste constitue le périmètre opérationnel du pilote. Elle remplace la sélection automatique par intersection de frontières. Le script sélectionne ces communes dans le référentiel communal wallon et échoue explicitement si l'une d'elles n'est pas retrouvée.

## Sources

- Limites communales wallonnes : référentiel communal du Géoportail de la Wallonie.
- Secteurs statistiques 2026 et population par secteur statistique 2026 : Statbel, fichiers open data. Jointure sur le code stable `cd_sector` / `CD_SECTOR`.
- ICU : service ArcGIS REST AWAC_VILLES_ALEAS_CHALEUR_ICU, couche 12 « Ilot de chaleur urbain (ICU) », et téléchargement GeoTIFF AWAC_ALEAS_CHALEUR_ICU en EPSG:31370.
- Licence de la donnée ICU : CC-BY 4.0. Les exports doivent conserver l'attribution au SPW/AWAC/ISSeP.

## Vérification des classes raster

La couche ICU est un raster catégoriel. Les codes ont été contrôlés en échantillonnant des pixels du GeoTIFF, puis en comparant leur couleur au rendu du service officiel et à sa légende :
- `0` : « Pas d'ICU » (transparent dans le rendu du service ; valeur déclarée NoData dans le GeoTIFF, mais interprétée ici comme absence d'ICU).
- `1` : « ICU très intense » (rouge foncé).
- `2` : « ICU intense » (rouge).

Le GeoTIFF téléchargé présente une grille native d'environ 83,54 × 83,46 m (aire de pixel : 6 971,84 m²), alors que la fiche catalogue annonce une résolution spatiale de 100 m. Les calculs utilisent la géotransformation effective du GeoTIFF, et non une taille de pixel supposée de 100 m.

## Indicateurs calculés

Calcul au niveau des secteurs statistiques, puis agrégation aux quartiers statistiques (`cd_quarter`) et aux communes.

- `icu_area_very_intense_m2` : surface ICU très intense (code 1).
- `icu_area_intense_m2` : surface ICU intense (code 2).
- `icu_area_total_m2` : somme des deux surfaces exposées.
- `icu_surface_pct` : surface ICU intense + très intense / surface couverte par le raster dans l'unité × 100.
- `population_total` : population Statbel 2026.
- `icu_population_est` : population du secteur × part surfacique ICU (hypothèse de répartition uniforme).
- `icu_population_pct` : population estimée exposée / population totale × 100.
- `raster_coverage_pct` : surface des pixels du raster situés dans l'unité / surface géométrique de l'unité × 100.

L'inclusion des pixels est fondée sur le centre du pixel dans le polygone (`all_touched=False)). Les classes 0, 1 et 2 participent au calcul de la surface couverte ; seules les classes 1 et 2 participent à la surface ICU.

## Résultats de contrôle du premier calcul

Le workflow GitHub Actions a terminé avec succès et produit les exports pour :
- 1 237 secteurs statistiques ;
- 433 quartiers statistiques ;
- 29 communes.

Pour les 29 communes :
- population Statbel totale : 381 527 habitants ;
- population rattachée à un secteur géolocalisable : 381 364 ;
- population non localisable dans un secteur : 163 habitants (22 lignes Statbel) ;
- surface ICU intense ou très intense estimée : 138,65 km² ;
- part de surface ICU sur la surface couverte : 5,51 % ;
- population estimée exposée : environ 198 719 habitants, soit 52,09 % de la population totale.

Les 163 personnes sans secteur géolocalisable sont conservées dans la population totale communale et dans le dénominateur du pourcentage communal, mais ne reçoivent pas artificiellement de valeur d'exposition. Les indicateurs de quartier et de secteur ne comprennent que les populations géolocalisées.

## Précautions et limites

1. Le millésime de la population est 2026, tandis que la couche ICU disponible a été mise à jour le 14/11/2024. Les résultats décrivent donc la population 2026 exposée selon la cartographie ICU AWAC disponible, et non une observation ICU de 2026.
2. Le prorata surfacique suppose une répartition uniforme de la population dans chaque secteur. Il s'agit d'une estimation exploratoire, pas d'un décompte de personnes géolocalisées. Une amélioration ultérieure pourrait répartir la population à partir des bâtiments résidentiels ou des logements.
3. Le calcul est réalisé en projection métrique EPSG:31370. Les GeoJSON d'export sont reprojetés en EPSG:4326.
4. Le dénominateur de la part de surface exposée est la surface raster couverte, y compris les pixels de classe 0 « Pas d'ICU ». La couverture raster est publiée séparément.
5. Les statistiques zonales utilisent les centres des pixels. Cette approche est reproductible et cohérente avec la grille raster, mais reste une approximation des intersections exactes en bordure des secteurs.
6. La couche ICU est une cartographie de vulnérabilité/climat urbain basée notamment sur les zones climatiques locales (LCZ) et les sorties UrbClim ; elle ne représente pas une mesure thermique instantanée.
7. Conserver les versions, dates de téléchargement, licences et URL des sources dans les exports.

## Fichiers générés

Dans `data/processed/` :
- `icu_exposure_sectors_2026.geojson` et `.csv`
- `icu_exposure_quartiers_2026.geojson` et `.csv`
- `icu_exposure_communes_2026.geojson` et `.csv`

Le contrôle qualité est enregistré dans `data/interim/icu_exposure_qa_2026.json`.

## Sources en ligne

- https://geoportail.wallonie.be/catalogue/4073341b-8587-4d71-bf07-b1f07c678b9d.html
- https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/12
- https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/legend
- https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/4073341b-8587-4d71-bf07-b1f07c678b9d/AWAC_ALEAS_CHALEUR_ICU_GEOTIFF_31370.zip
- https://statbel.fgov.be/fr/open-data?category=209
