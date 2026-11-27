# Installation en entreprise via Artifactory : notes du mainteneur

La procédure d'installation pour les organisations qui utilisent JFrog Artifactory est
[CUSTOMER-INSTALL.fr.md](CUSTOMER-INSTALL.fr.md) (anglais : [CUSTOMER-INSTALL.md](CUSTOMER-INSTALL.md)). Cette page
garde ce que le guide client laisse de côté : l'installation par script de l'étape 9, une note sur Artifactory Pro,
et le compte rendu complet des tests du lab. Les numéros d'étapes ci-dessous sont ceux du guide client jusqu'à
l'étape 8.

English version: [INSTALL-ENTERPRISE.md](INSTALL-ENTERPRISE.md).

## Installation par script au lieu des étapes manuelles

Le guide client installe par l'interface de VCF Operations. Les scripts d'installation de ce dépôt font la même
chose avec un Chrome headless et des appels internes à l'interface de VCF Operations, non pris en charge et
susceptibles de changer à chaque version ([INSTALL.fr.md](INSTALL.fr.md)). Ils remplacent l'étape 9 du guide
client ; les étapes 10 et 11 restent. VCF Operations ne lit pas les signatures cosign de l'étape 4 : c'est donc le
script d'installation qui les vérifie (`install/verify_pak.sh`).

Donner au script le SHA-256 enregistré à l'étape 7. Il refuse alors tout autre fichier. Pour qu'il vérifie aussi la
signature de l'image de l'adaptateur via Artifactory, définir `FEDMP_VERIFY=1` et `FEDMP_IMAGE_REPO`, après y avoir
copié les signatures (`cosign copy`, étape 6). Lancer d'abord la vérification seule, avant d'activer l'option :

```bash
export FEDMP_PAK_SHA256=<the SHA-256 of step 7>
export FEDMP_VERIFY=1 FEDMP_IMAGE_REPO=$ART/$APPROVED/nsxfederation    # seulement après le cosign copy de l'étape 6
install/verify_pak.sh NSXFederation_${V}_site.pak                     # doit se terminer sans erreur
install/upi.sh enableUPI
install/install_unsigned.sh NSXFederation_${V}_site.pak               # vérifie à nouveau, installe, désactive l'option
```

Sans `FEDMP_PAK_SHA256` et `FEDMP_VERIFY=1`, le pack réécrit n'a pas de bundle et est installé avec un
avertissement.

## Artifactory Pro

Avec Artifactory Pro, l'API de promotion Docker ou `jf rt docker-promote` peut faire la copie de l'étape 6 à
l'intérieur d'Artifactory. Ce cas n'a pas été testé ici ; vérifier ensuite les digests de la même façon.

## Ce qui a été testé

Le 2026-11-26, sur un JFrog Container Registry 7.161.26 avec PostgreSQL, derrière nginx avec un certificat émis
par une autorité de test, avec skopeo 1.25.1 et cosign 2.4.1 :

- Une image Docker Hub récupérée via un dépôt distant Docker Hub : même digest que sur Docker Hub.
- L'image publiée de l'adaptateur `v1.13.1` récupérée via un dépôt distant (de `ghcr.io`, le seul endroit où elle
  était publiée à ce moment), et sa signature vérifiée via Artifactory, avec la clé de cette version.
- La version `v1.13.3`, la première publiée sur Docker Hub, passée par les étapes 3, 4, 6 et 7 telles qu'écrites :
  image du pack récupérée via le dépôt distant Docker Hub et pack extrait (somme de contrôle OK), signatures sans
  clé de l'image du pack et de l'image de l'adaptateur vérifiées via Artifactory, les deux images promues avec les
  mêmes digests (`nsxfederation-pak` `sha256:1ff264cf...`, `nsxfederation` `sha256:bd533626...`), pack réécrit
  vers le dépôt des images approuvées avec le digest de l'adaptateur inchangé.
- Les deux images copiées dans un dépôt local avec `skopeo copy --all --preserve-digests` : mêmes digests. Sans
  `--all`, l'image du pack est sortie avec un autre digest ; c'est pourquoi l'étape 6 l'utilise.
- Le pack extrait de l'image du pack dans Artifactory, somme de contrôle OK, et réécrit avec `repack.py` vers le
  dépôt local.

- La version `v1.13.5`, la première avec des attestations, via l'Artifactory du lab (JFrog Container Registry
  7.161.26 sur `https://10.0.6.28`, dépôt distant Docker Hub) : signatures sans clé des deux images et attestation
  SBOM signée (46 composants) vérifiées via Artifactory, et provenance de construction de la copie Docker Hub
  vérifiée avec `gh attestation verify`.

- Docker seul, avec Docker Engine 29.6.2 et le magasin d'images containerd : les deux images `v1.13.5` récupérées
  sur Docker Hub par digest, taguées et poussées vers un registre de test ont gardé leurs digests. Le pack extrait
  avec `docker create` et `docker cp` a passé sa somme de contrôle.
- Le transfert sans accès à Internet : les deux images `v1.13.5` passées par des dossiers `dir:` et un fichier tar
  vers un registre de test, mêmes digests. Via `oci-archive:`, l'image du pack est sortie avec un autre digest.
- `verify_pak.sh` avec `FEDMP_PAK_SHA256` : la bonne valeur passe, toute autre valeur arrête l'installation, avec
  et sans `FEDMP_VERIFY`.

- La version `v1.13.6` par l'Artifactory du lab, étapes 2 à 7 telles qu'écrites : image du pack `sha256:e241b3f2...`
  et image de l'adaptateur `sha256:15cd0535...`, les deux signatures keyless, l'attestation SBOM et la provenance
  vérifiées, les deux images promues avec les mêmes digests, pack réécrit avec le digest de l'adaptateur
  inchangé. Puis la vérification de l'étape 9 avant l'activation de l'option : signatures copiées dans
  `vcfops-approved` avec `cosign copy`, et `verify_pak.sh` avec `FEDMP_PAK_SHA256`, `FEDMP_VERIFY=1` et
  `FEDMP_IMAGE_REPO` pointant vers `vcfops-approved` a réussi ; avec la somme de contrôle d'un autre fichier, il a
  refusé le pack.

Pas encore testé, ce mode n'a donc pas été exécuté de bout en bout :

- La lecture anonyme sur `vcfops-approved` (étape 1, point 3) et la récupération par un proxy cloud depuis
  Artifactory (étape 8). L'édition gratuite ne règle les permissions que dans son interface. La même récupération
  depuis un registre du lab fonctionne, et une récupération sans la permission a été refusée (`unauthorized`),
  comme prévu.
- L'installation du pack `_site` depuis Artifactory et le compte (étape 9), le retour arrière et la
  désinstallation.
- JFrog Xray, l'API de promotion d'Artifactory Pro et la méthode d'accès Subdomain.
