# Pilote ICU — communes wallonnes frontalières avec la France

## Objectif
Estimer, pour les communes wallonnes partageant une limite terrestre avec la France, la part de surface et la population exposées aux classes d'îlot de chaleur urbain (ICU) cartographiées par l'AWAC.

## Sources
- Limites communales wallonnes : service ArcGIS REST du Géoportail de la Wallonie, couche Commune (ID 3), source AGDP.
- Communes françaises : API officielle geo.api.gouv.fr, départements français limitrophes de la Belgique.
- Secteurs statistiques 2026 et population par secteur statistique 2026 : Statbel, fichiers open data.
- ICU : service ArcGIS REST AWAC_VILLES_ALEAS_CHALEUR_ICU, couche 12 « Ilot de chaleur urbain (ICU) », raster en EPSG:31370.

## Définition du territoire pilote
Une commune wallonne est retenue si sa limite polygonale partage une longueur de frontière non nulle avec au moins une commune française. Le script utilise les communes françaises des départements 02, 08, 54, 55, 57 et 59 comme candidats géographiques, puis vérifie l'adjacence géométrique. Cette sélection est à contrôler visuellement et à comparer au référentiel national CadGIS avant publication définitive.

## Indicateurs
Pour chaque secteur statistique puis par agrégation communale :
- surface_secteur_ha : surface du secteur.
- surface_icu_intense_ha et surface_icu_tres_intense_ha : surface couverte par les classes ICU intenses.
- surface_icu_totale_ha : somme des deux classes.
- part_surface_icu_pct : surface ICU intense ou très intense / surface valide couverte par le raster × 100.
- population_2026 : population Statbel du secteur.
- population_estimee_exposee : population du secteur × part de surface ICU.
- part_population_exposee_pct : population estimée exposée / population totale × 100.

## Précautions
1. Le millésime de la population est 2026, tandis que la couche ICU disponible est celle du service AWAC consulté. Le résultat décrit donc la population 2026 exposée selon la cartographie ICU source, et non une observation ICU de 2026.
2. Le prorata surfacique suppose une répartition uniforme de la population dans chaque secteur. Il s'agit d'une estimation exploratoire, pas d'un décompte de personnes géolocalisées.
3. Les surfaces doivent être calculées dans une projection métrique (EPSG:31370).
4. Le dénominateur de la part de surface exposée est la surface effectivement couverte par des pixels ICU valides. La couverture raster doit être publiée séparément.
5. La couche ICU est un raster, pas une couche polygonale. La lecture des valeurs/classes doit être vérifiée à partir des métadonnées et de la légende du service avant de publier des chiffres.
6. Conserver les versions, dates de téléchargement, licences et URL des sources dans les exports.

## Sources en ligne
- https://geoservices.wallonie.be/arcgis/rest/services/CHGMT_CLIMAT/AWAC_VILLES_ALEAS_CHALEUR_ICU/MapServer/12
- https://geoservices.wallonie.be/arcgis/rest/services/LIMITES/LIMITES_ADMINISTRATIVES/MapServer/3
- https://statbel.fgov.be/fr/open-data?category=209
- https://geo.api.gouv.fr/decoupage-administratif/communes
