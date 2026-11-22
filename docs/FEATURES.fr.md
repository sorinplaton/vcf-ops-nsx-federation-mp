# Fonctionnalités et alertes du pack NSX Federation, version 1.9.0

Généré par `tools/gen_features.py` à partir du modèle d'objets et des tables d'alertes du pack. Ne pas modifier à la main. Version anglaise : [FEATURES.md](FEATURES.md).

Les noms des métriques, des propriétés, des alertes et des symptômes, ainsi que les recommandations, restent en anglais : c'est ainsi que VCF Operations les affiche.

## Résumé

- 15 types d'objet, 138 métriques dans 17 groupes, 120 propriétés, 22 indicateurs clés.
- 71 définitions d'alerte, 116 symptômes, 40 recommandations. 14 alertes pèsent sur le badge Risk, les autres sur Health.
- 4 groupes personnalisés, 5 tableaux de bord, 5 vues en liste, 1 rapport, 1 arborescence d'inventaire, 1 icône par type d'objet.
- Collecte en lecture seule depuis NSX. Les réglages du compte, l'approbation des certificats, le dimensionnement et la mise à niveau sont dans [OPERATIONS.md](OPERATIONS.md) (en anglais).

## Ce que fait la collecte

- Trouve le Global Manager actif à chaque cycle et suit un basculement quand le compte liste les deux Global Managers.
- Découvre les Local Managers depuis le Global Manager et compare chaque certificat à l'empreinte que détient le Global Manager, ou (confiance des certificats ca) à une autorité de certification de confiance et à l'adresse ; chaque connexion est liée au certificat vérifié.
- Identifie les edge nodes et les tunnels RTEP par l'ID NSX du transport node, et les canaux de synchronisation par l'ID de site : un renommage garde l'objet et son historique.
- Continue de collecter les Local Managers quand aucun Global Manager ne répond, à partir de la liste des emplacements des cycles précédents (cache de l'adaptateur, puis VCF Operations).
- Signale un appel NSX en échec comme une donnée inconnue : rien n'est envoyé pour les valeurs concernées, et l'appel est listé sur l'objet fédération.
- Respecte un quota d'appels par manager, ralentit sur les réponses HTTP 429 et 503, retente une fois après un dépassement de délai et ignore pour le reste du cycle un manager qui ne répond toujours pas.
- Calcule des taux à partir des compteurs NSX, et des agrégats pour toute la fédération.
- Lit la latence et le remplissage des files de synchronisation de chaque emplacement dans les détails des flux du Global Manager, et compare la latence au RTT maximal défini dans NSX.
- Calcule la part des paquets rejetés par chaque tunnel RTEP, et le taux de coupures des sessions BGP du Tier-0 et des sessions BGP intersites.
- Lit la réalisation de chaque objet étendu à chaque cycle jusqu'à 200 objets, et par balayage sur six cycles au-delà.
- Laisse les alarmes de fédération d'un Local Manager à l'intégration NSX native quand celle-ci le supervise, et relie ses nœuds Edge et ses clusters Edge aux objets natifs.
- Garde silencieuses les alertes des objets dépendants tant qu'un emplacement est coupé du Global Manager.
- Dit si un Global Manager standby peut prendre le relais : prêt, pas prêt avec les raisons (synchronisation avec l'actif, état du cluster, version, certificat expiré, mise à niveau en cours ou en pause) ou inconnu quand une valeur n'a pas pu être lue. La métrique vaut 1 seulement quand c'est confirmé.
- Supprime en option les objets qui ont quitté NSX (option désactivée par défaut).

## Types d'objet

| Type d'objet | Clé | Métriques | Propriétés | Définitions d'alerte |
| --- | --- | --- | --- | --- |
| NSX Federation | `federation` | 29 | 12 | 9 |
| Global Manager | `global_manager` | 15 | 16 | 22 |
| Manager Node | `manager_node` | 1 | 2 | 0 |
| Location | `location` | 24 | 22 | 24 |
| Sync Channel | `sync_channel` | 1 | 5 | 1 |
| Federation Edge Cluster | `edge_cluster` | 4 | 5 | 1 |
| Federation Edge Node | `edge_node` | 4 | 2 | 1 |
| RTEP Tunnel | `rtep_tunnel` | 21 | 5 | 5 |
| Stretched Tier-0 Gateway | `tier0` | 4 | 8 | 1 |
| Stretched Tier-1 Gateway | `tier1` | 4 | 8 | 1 |
| Stretched Segment | `segment` | 4 | 9 | 1 |
| Global Security Policy | `global_policy` | 8 | 9 | 1 |
| Stretched Object at Location | `realization` | 6 | 5 | 1 |
| Tier-0 BGP Neighbor | `bgp_neighbor` | 8 | 8 | 3 |
| Global Firewall Rule | `rule` | 5 | 4 | 0 |

## Seuils

Les symptômes ci-dessous comparent une métrique à un nombre ou à la plage habituelle de la métrique. Tous les autres symptômes testent un état : un compteur supérieur à 0 (sessions tombées, nœuds hors ligne, objets en erreur), une valeur inférieure à 1 (non connecté, non établi, non réalisé) ou la valeur d'une propriété (état du cluster, état de la mise à niveau, mode). « Déclenchement / effacement » donne le nombre de cycles de collecte pendant lesquels la condition doit rester vraie avant que le symptôme se déclenche, puis le nombre de cycles pendant lesquels elle doit avoir disparu avant qu'il s'efface.

| Type d'objet | Métrique | Seuil | Criticité | Déclenchement / effacement (cycles) | Alerte |
| --- | --- | --- | --- | --- | --- |
| NSX Federation | Collection duration (`collection\|collection_duration_s`) | supérieur à 240 s | Warning | 2 / 2 | NSX Federation collection is slow or throttled |
| Global Manager | Hours since last successful backup (`backup\|backup_age_hours`) | supérieur à 24 heures | Immediate | 1 / 1 | Global Manager backup is missing, old or failed |
| Global Manager | Days until the first certificate expires (`certificates\|cert_min_days_to_expiry`) | inférieur à 30 jours | Warning | 1 / 1 | Global Manager certificate is about to expire |
| Global Manager | Days until the first certificate expires (`certificates\|cert_min_days_to_expiry`) | inférieur à 7 jours | Critical | 1 / 1 | Global Manager certificate is about to expire |
| Location | Hours since last successful backup (`backup\|backup_age_hours`) | supérieur à 24 heures | Warning | 1 / 1 | Local Manager backup is missing, old or failed |
| Location | Days until the first certificate expires (`certificates\|cert_min_days_to_expiry`) | inférieur à 30 jours | Warning | 1 / 1 | Local Manager certificate is about to expire |
| Location | Days until the first certificate expires (`certificates\|cert_min_days_to_expiry`) | inférieur à 7 jours | Critical | 1 / 1 | Local Manager certificate is about to expire |
| RTEP Tunnel | Received packets dropped per minute (`rates\|rx_dropped_per_min`) | supérieur à 10 par minute | Warning | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Sent packets dropped per minute (`rates\|tx_dropped_per_min`) | supérieur à 10 par minute | Warning | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Share of received packets dropped (`rates\|rx_drop_pct`) | supérieur à 1 % | Warning | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Share of sent packets dropped (`rates\|tx_drop_pct`) | supérieur à 1 % | Warning | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Share of received packets dropped (`rates\|rx_drop_pct`) | supérieur à 5 % | Immediate | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Share of sent packets dropped (`rates\|tx_drop_pct`) | supérieur à 5 % | Immediate | 2 / 2 | RTEP tunnel is dropping packets |
| RTEP Tunnel | Inter-site BGP connection drops per hour (`connection_drops_per_hour`) | supérieur à 0 par heure | Warning | 1 / 2 | RTEP tunnel inter-site BGP session is flapping |
| RTEP Tunnel | Received packets blocked per minute (`rates\|rx_blocked_per_min`) | supérieur à 10 par minute | Warning | 2 / 2 | RTEP tunnel is blocking packets |
| RTEP Tunnel | Sent packets blocked per minute (`rates\|tx_blocked_per_min`) | supérieur à 10 par minute | Warning | 2 / 2 | RTEP tunnel is blocking packets |
| Tier-0 BGP Neighbor | Connection drops per hour (`connection_drops_per_hour`) | supérieur à 0 par heure | Warning | 1 / 2 | Tier-0 BGP neighbor of a stretched gateway is flapping |
| Location | Round-trip time to the Global Manager (`connectivity\|gm_rtt_ms`) | supérieur à 150 ms | Warning | 2 / 2 | Round-trip time to the Global Manager is high |
| Location | Round-trip time to the Global Manager, % of the maximum RTT setting (`connectivity\|gm_rtt_pct_of_max`) | supérieur à 80 % | Warning | 2 / 2 | Round-trip time to the Global Manager is high |
| Location | Round-trip time to the Global Manager, % of the maximum RTT setting (`connectivity\|gm_rtt_pct_of_max`) | supérieur à 100 % | Immediate | 2 / 2 | Round-trip time to the Global Manager is high |
| RTEP Tunnel | Received bytes per second (`rates\|rx_bytes_per_s`) | appris par VCF Operations (seuil dynamique) | Warning | 3 / 3 | RTEP tunnel traffic is above its usual range |
| RTEP Tunnel | Sent bytes per second (`rates\|tx_bytes_per_s`) | appris par VCF Operations (seuil dynamique) | Warning | 3 / 3 | RTEP tunnel traffic is above its usual range |
| Location | Round-trip time to the Global Manager (`connectivity\|gm_rtt_ms`) | appris par VCF Operations (seuil dynamique) | Warning | 3 / 2 | Round-trip time to the Global Manager is high |

Les alertes issues des alarmes de fédération de NSX utilisent les seuils de NSX lui-même, par exemple une latence au-dessus de la limite de NSX pendant plus de 2 minutes, ou une synchronisation entre emplacements en échec depuis 3 minutes (Warning) ou 15 minutes (Immediate). La façon de changer un seuil du pack est décrite dans [OPERATIONS.md](OPERATIONS.md#changing-a-threshold) (en anglais).

## Alertes

La criticité indiquée est la plus haute parmi celles des symptômes qui déclenchent l'alerte. Les « cycles » sont des cycles de collecte (5 minutes par défaut) ; sauf mention contraire, une alerte se déclenche et s'efface en un cycle.

### NSX Federation

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| NSX Federation has no active Global Manager | Critical | Health | No Global Manager is in the active role ("Global Managers claiming active" inférieur à 1) | Open the Global Manager UI (System, Location Manager) and check which Global Manager is active. If the active Global Manager is lost, follow the NSX Federation disaster recovery procedure and make the standby active. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html |
| NSX Federation split-brain: two active Global Managers | Critical | Health | More than one Global Manager claims the active role ("Global Managers claiming active" supérieur à 1) ; ou NSX reports a split-brain between the Global Managers ("Split-brain reported by NSX (1 = yes)" supérieur à 0) | Two Global Managers claim the active role. Do not change global configuration. Decide which Global Manager holds the valid configuration and force the other one to standby, as described in the NSX Federation recovery documentation. See https://knowledge.broadcom.com/external/article/324244 |
| Global Manager is not reachable by the collector | Immediate | Health | A configured Global Manager is not reachable by the collector ("Global Managers not reachable" supérieur à 0) | Check that the Global Manager appliance is powered on and that the cloud proxy can reach it on TCP 443. If its certificate was replaced, update the trusted certificate of the adapter instance, or switch certificate trust to 'ca' and add the issuing CA. |
| NSX Federation has no standby Global Manager | Warning | Risk | No standby Global Manager is configured ("Standby Global Manager configured" vaut False) | Deploy a standby Global Manager in another site and add it under System, Location Manager, Add Standby Global Manager. Without a standby the global configuration cannot be changed if the active Global Manager is lost. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html |
| NSX Federation collection is partial | Warning | Risk | A manager used its whole API call budget ("Managers that used their call budget" supérieur à 0) | The collection of this cycle is partial. Raise 'API call budget per manager and cycle' in the advanced settings of the adapter instance, or lengthen the collection interval. |
| NSX Federation collection has data gaps | Warning | Risk | NSX API calls of the collection got no answer ("NSX API calls without an answer (data unknown)" supérieur à 0). Se déclenche après 2 cycles, s'efface après 2 | Some NSX API calls of the collection failed, so part of the data is unknown and the alerts that depend on it are not evaluated. Read the property 'NSX API calls without an answer' on the federation object: it lists the manager, the call and the HTTP status. HTTP 403 points to missing rights of the collection account, HTTP 5xx or a timeout to a manager under load or a service that is down. |
| NSX Federation collection is slow or throttled | Warning | Risk | NSX throttles the API calls of the collection ("NSX API calls throttled" supérieur à 0) ; ou The collection takes more than 4 minutes ("Collection duration" supérieur à 240). Se déclenche après 2 à 3 cycles selon la condition, s'efface après 2 | The collection is close to the limits of the managers or of the collection interval. Lengthen the collection interval of the account, reduce the policies whose rules are collected, or lower 'Managers collected in parallel'. Check the load of the manager named in the NSX alarms. |
| NSX Federation collection runs without a Global Manager | Immediate | Health | The collection runs in degraded mode ("Collection mode" contient degraded) | No Global Manager answered, none is active, or the active one did not return its locations (the property 'Collection mode' says which), so the collection uses the list of locations it cached earlier and collects the Local Managers only. Check the Global Manager addresses of the account and the alert on the Global Managers. |
| NSX Federation account lists a Global Manager of another federation | Warning | Health | A listed Global Manager belongs to another federation ("Listed Global Managers of another federation" supérieur à 0) | One account monitors one federation. Remove the address named in the property 'Global Managers not collected' from this account and create a second account for the other federation. |

### Global Manager

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Global Manager has no role in the federation | Immediate | Health | Global Manager has no role (mode NONE) ("Mode" vaut NONE) | A Global Manager that comes back after a failover has no role until it is added again as the standby. In the UI of the active Global Manager, open System, Location Manager and make it the standby. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/disaster-recovery-using-federation.html |
| Global Managers run different versions | Warning | Risk | Global Manager version differs from the active Global Manager ("Same version as the active Global Manager" vaut False) | Upgrade the Global Manager that is behind. NSX refuses a switchover while the active and the standby Global Manager run different versions. See https://knowledge.broadcom.com/external/article/449461 |
| NSX upgrade in progress, paused or failed on the Global Manager | Immediate | Risk | An NSX upgrade is in progress on the Global Manager ("Upgrade status" vaut IN_PROGRESS) ; ou An NSX upgrade is paused on the Global Manager ("Upgrade status" vaut PAUSED) ; ou An NSX upgrade failed on the Global Manager ("Upgrade status" vaut FAILED) | Finish the upgrade in the order NSX Federation requires: the standby Global Manager, then the active Global Manager, then the Local Managers. A Local Manager must not run a newer release than the Global Manager. Take a backup of every manager cluster first. A paused upgrade is resumed or cancelled in System, Upgrade. See https://knowledge.broadcom.com/external/article/435153 |
| Global Manager cluster is degraded | Critical | Health | Global Manager cluster status is not STABLE ("Cluster status" différent de STABLE) ; ou A Global Manager node is offline ("Nodes offline" supérieur à 0) | Open System, Appliances on the manager and check the state of each node and service group. Restore or redeploy the failed node. |
| Standby Global Manager is not in sync | Immediate | Health | A standby Global Manager is not in sync ("Standby sync healthy (1 = in sync)" inférieur à 1). Se déclenche après 3 cycles, s'efface après 2 | Check the connectivity between the active and the standby Global Manager (ping, then TCP 443 and 1236) and the state of both Global Manager clusters. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| Global Manager backup is missing, old or failed | Immediate | Risk | Backup is not configured on the Global Manager ("Backup enabled" vaut False) ; ou Last successful Global Manager backup is older than 24 hours ("Hours since last successful backup" supérieur à 24) ; ou Last Global Manager backup failed ("Last backup result" vaut FAILED) ; ou No backup of the Global Manager has run ("Last backup result" vaut NONE) et "Backup enabled" différent de False | Configure a backup target and a schedule under System, Backup and Restore on the manager, then run a backup and confirm that it succeeds. A manager can only be restored from its own backup. |
| Global Manager certificate is about to expire | Critical | Risk | A Global Manager certificate expires in less than 30 days ("Days until the first certificate expires" inférieur à 30) ; ou A Global Manager certificate expires in less than 7 days ("Days until the first certificate expires" inférieur à 7) | Replace the certificate before it expires (System, Certificates). An expired manager certificate breaks the connection between the Global Manager and the Local Managers. See https://knowledge.broadcom.com/external/article/429189 |
| NSX alarm: Global Manager split-brain | Critical | Health | Global Manager raised NSX alarm gm_to_gm_split_brain | Two Global Managers claim the active role. Do not change global configuration. Decide which Global Manager holds the valid configuration and force the other one to standby, as described in the NSX Federation recovery documentation. See https://knowledge.broadcom.com/external/article/324244 |
| NSX alarm: active to standby Global Manager sync error | Immediate | Health | Global Manager raised NSX alarm gm_to_gm_synchronization_error | Check the connectivity between the active and the standby Global Manager (ping, then TCP 443 and 1236) and the state of both Global Manager clusters. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: active to standby Global Manager sync warning | Warning | Health | Global Manager raised NSX alarm gm_to_gm_synchronization_warning | Check the connectivity between the active and the standby Global Manager (ping, then TCP 443 and 1236) and the state of both Global Manager clusters. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: latency between Global Managers is high | Warning | Health | Global Manager raised NSX alarm gm_to_gm_latency_warning | Measure the round-trip time between the two managers named in the alarm. Broadcom documents 150 ms as the limit between locations that share stretched networks (500 ms otherwise); look for WAN congestion or a path change. See https://knowledge.broadcom.com/external/article/345822 |
| NSX alarm: Global Manager to Local Manager sync error | Immediate | Health | Global Manager raised NSX alarm gm_to_lm_synchronization_error. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and that the async-replicator service runs on both sides (get service async_replicator). Its log is /var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: Global Manager to Local Manager sync warning | Warning | Health | Global Manager raised NSX alarm gm_to_lm_synchronization_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and that the async-replicator service runs on both sides (get service async_replicator). Its log is /var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: latency between Global Manager and Local Manager is high | Warning | Health | Global Manager raised NSX alarm gm_to_lm_latency_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Measure the round-trip time between the two managers named in the alarm. Broadcom documents 150 ms as the limit between locations that share stretched networks (500 ms otherwise); look for WAN congestion or a path change. See https://knowledge.broadcom.com/external/article/345822 |
| NSX alarm: sync between locations failed for 15 minutes | Immediate | Health | Global Manager raised NSX alarm lm_to_lm_synchronization_error. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Run get site-replicator remote-sites on the Local Managers to see the connection state between the locations, then check TCP 1236 between them. A location that is connected but not synchronized may still be electing its leader; check again after a few seconds. |
| NSX alarm: sync between locations failed for 3 minutes | Warning | Health | Global Manager raised NSX alarm lm_to_lm_synchronization_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Run get site-replicator remote-sites on the Local Managers to see the connection state between the locations, then check TCP 1236 between them. A location that is connected but not synchronized may still be electing its leader; check again after a few seconds. |
| NSX alarm: federation sync queue above threshold | Warning | Health | Global Manager raised NSX alarm queue_occupancy_threshold_exceeded. Silencieuse tant qu'un emplacement dont elle dépend est en panne | The sync queue grows when the remote location cannot keep up or cannot be reached. Check the connectivity to the remote location and /var/log/async-replicator/ar.log. See https://knowledge.broadcom.com/external/article/414606 |
| NSX alarm: Local Manager restored during config import | Immediate | Health | Global Manager raised NSX alarm lm_restore_while_config_import_in_progress. Silencieuse tant qu'un emplacement dont elle dépend est en panne | The location is in an inconsistent state. Delete the onboarding status of the location on the Global Manager, as the NSX alarm describes, then start the configuration import again. |
| NSX alarm: RTEP BGP neighbor down | Immediate | Health | Global Manager raised NSX alarm rtep_bgp_down. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| NSX alarm: RTEP connectivity lost | Immediate | Health | Global Manager raised NSX alarm rtep_connectivity_lost. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| NSX alarm: delta sync with the directory failed | Critical | Health | Global Manager raised NSX alarm error_in_delta_sync. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Look first for an alarm "connectivity to the LDAP server lost" on the same manager. Then read /var/log/syslog on the manager around the time of the alarm and search for "Error happened when synchronize LDAP objects". Ask the Active Directory administrator about recent changes in the domain named in the alarm. |
| NSX alarm: connectivity to the LDAP server lost | Critical | Health | Global Manager raised NSX alarm connectivity_to_ldap_server_lost. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check that the LDAP server named in the alarm runs and is reachable from the Global Manager nodes, that no firewall blocks the path, and that its details are right in NSX. Then use Test Connection under Identity Firewall AD in the Global Manager UI. |

### Location

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Replication between the location and the Global Manager is in error | Immediate | Health | A replication flow with the Global Manager is in an error state ("Replication flows in an error state" supérieur à 0). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and that the async-replicator service runs on both sides (get service async_replicator). Its log is /var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| Location runs a newer NSX release than the Global Manager | Immediate | Risk | Location runs a newer NSX release than the Global Manager ("NSX release newer than the Global Manager" vaut True) | Finish the upgrade in the order NSX Federation requires: the standby Global Manager, then the active Global Manager, then the Local Managers. A Local Manager must not run a newer release than the Global Manager. Take a backup of every manager cluster first. A paused upgrade is resumed or cancelled in System, Upgrade. See https://knowledge.broadcom.com/external/article/435153 |
| NSX upgrade in progress, paused or failed on the Local Manager | Immediate | Risk | An NSX upgrade is in progress on the Local Manager ("Upgrade status" vaut IN_PROGRESS) ; ou An NSX upgrade is paused on the Local Manager ("Upgrade status" vaut PAUSED) ; ou An NSX upgrade failed on the Local Manager ("Upgrade status" vaut FAILED) | Finish the upgrade in the order NSX Federation requires: the standby Global Manager, then the active Global Manager, then the Local Managers. A Local Manager must not run a newer release than the Global Manager. Take a backup of every manager cluster first. A paused upgrade is resumed or cancelled in System, Upgrade. See https://knowledge.broadcom.com/external/article/435153 |
| Location lost its connection to the active Global Manager | Critical | Health | Location is not connected to the active Global Manager ("Connected to the active Global Manager" inférieur à 1) | Check the network path between the active Global Manager and the Local Manager of this location (TCP 443 and 1236 in both directions) and the state of the Local Manager cluster. The location keeps forwarding traffic with its last configuration. |
| Local Manager is not reachable by the collector | Warning | Health | The collector cannot reach the Local Manager ("Reached by the collector" inférieur à 1) | Check that the cloud proxy can reach the Local Manager on TCP 443 and that the account of the adapter instance is valid on this Local Manager. This is the monitoring path only. |
| Local Manager certificate is not trusted | Immediate | Risk | The Local Manager certificate is not the trusted one ("Certificate trust" contient NOT TRUSTED) | The Local Manager presents a certificate that is not the one the Global Manager holds for it. Confirm the certificate change is expected, then update the location in the Global Manager so the thumbprint matches. With certificate trust 'ca', also check that the certificate chains to a CA of the adapter instance and that its subject alternative names include the address used (see the Collector error property). |
| Full sync to the location did not complete | Immediate | Health | A full sync did not complete ("Full syncs not completed" supérieur à 0). Se déclenche après 2 cycles, s'efface après 1 | Open the location in System, Location Manager and read the sync status details. Start a full sync again from the Global Manager once the cause (connectivity, version, disk space) is fixed. |
| Inter-site BGP sessions of the location are down | Critical | Health | Inter-site BGP sessions are not established ("Inter-site BGP sessions not established" supérieur à 0) ; ou No inter-site BGP session is established ("Inter-site BGP sessions established" inférieur à 1) et "Inter-site BGP sessions" au moins 1. Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| Round-trip time to the Global Manager is high | Immediate | Health | Round-trip time to the Global Manager is above 150 ms ("Round-trip time to the Global Manager" supérieur à 150) ; ou Round-trip time to the Global Manager is above 80% of the maximum RTT setting ("Round-trip time to the Global Manager, % of the maximum RTT setting" supérieur à 80) ; ou Round-trip time to the Global Manager is above the maximum RTT setting ("Round-trip time to the Global Manager, % of the maximum RTT setting" supérieur à 100) ; ou Round-trip time to the Global Manager is above its usual range (seuil dynamique). Se déclenche après 2 à 3 cycles selon la condition, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Measure the round-trip time between the Global Manager and the Local Manager of this location. Broadcom documents 150 ms as the limit between locations that share stretched networks (500 ms otherwise), and the maximum RTT setting of the location (System, Location Manager) is the limit NSX applies. Look for WAN congestion, a path change or a degraded link. See https://knowledge.broadcom.com/external/article/345822 |
| Location and Global Manager run different NSX releases | Info | Risk | Location version differs from the Global Manager ("Same version as the Global Manager" vaut False) | The location and the Global Manager run different NSX releases (the first three numbers of the version). That is expected only during an upgrade. Complete the upgrade so that all managers run the same release. A different patch level of the same release does not raise this alert. |
| Local Manager backup is missing, old or failed | Warning | Risk | Backup is not configured on the Local Manager ("Backup enabled" vaut False) ; ou Last successful Local Manager backup is older than 24 hours ("Hours since last successful backup" supérieur à 24) ; ou Last Local Manager backup failed ("Last backup result" vaut FAILED) ; ou No backup of the Local Manager has run ("Last backup result" vaut NONE) et "Backup enabled" différent de False | Configure a backup target and a schedule under System, Backup and Restore on the manager, then run a backup and confirm that it succeeds. A manager can only be restored from its own backup. |
| Local Manager certificate is about to expire | Critical | Risk | A Local Manager certificate expires in less than 30 days ("Days until the first certificate expires" inférieur à 30) ; ou A Local Manager certificate expires in less than 7 days ("Days until the first certificate expires" inférieur à 7) | Replace the certificate before it expires (System, Certificates). An expired manager certificate breaks the connection between the Global Manager and the Local Managers. See https://knowledge.broadcom.com/external/article/429189 |
| Local Manager cluster is degraded | Immediate | Health | Local Manager cluster status is not STABLE ("Local Manager cluster status" différent de STABLE) ; ou A Local Manager node is offline ("Local Manager nodes offline" supérieur à 0) | Open System, Appliances on the manager and check the state of each node and service group. Restore or redeploy the failed node. |
| NSX alarm: Global Manager to Local Manager sync error | Immediate | Health | Local Manager raised NSX alarm gm_to_lm_synchronization_error. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and that the async-replicator service runs on both sides (get service async_replicator). Its log is /var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: Global Manager to Local Manager sync warning | Warning | Health | Local Manager raised NSX alarm gm_to_lm_synchronization_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the connectivity between the Global Manager and the location (ping, TCP 1236 in both directions) and that the async-replicator service runs on both sides (get service async_replicator). Its log is /var/log/async-replicator/ar.log. See https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/advanced-network-management/administration-guide/managing-nsx-t-in-multiple-locations/nsx-t-federation/using-the-gm-and-lm-ui/monitoring-nsx-federation-locations.html |
| NSX alarm: latency between Global Manager and Local Manager is high | Warning | Health | Local Manager raised NSX alarm gm_to_lm_latency_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Measure the round-trip time between the two managers named in the alarm. Broadcom documents 150 ms as the limit between locations that share stretched networks (500 ms otherwise); look for WAN congestion or a path change. See https://knowledge.broadcom.com/external/article/345822 |
| NSX alarm: sync between locations failed for 15 minutes | Immediate | Health | Local Manager raised NSX alarm lm_to_lm_synchronization_error. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Run get site-replicator remote-sites on the Local Managers to see the connection state between the locations, then check TCP 1236 between them. A location that is connected but not synchronized may still be electing its leader; check again after a few seconds. |
| NSX alarm: sync between locations failed for 3 minutes | Warning | Health | Local Manager raised NSX alarm lm_to_lm_synchronization_warning. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Run get site-replicator remote-sites on the Local Managers to see the connection state between the locations, then check TCP 1236 between them. A location that is connected but not synchronized may still be electing its leader; check again after a few seconds. |
| NSX alarm: federation sync queue above threshold | Warning | Health | Local Manager raised NSX alarm queue_occupancy_threshold_exceeded. Silencieuse tant qu'un emplacement dont elle dépend est en panne | The sync queue grows when the remote location cannot keep up or cannot be reached. Check the connectivity to the remote location and /var/log/async-replicator/ar.log. See https://knowledge.broadcom.com/external/article/414606 |
| NSX alarm: Local Manager restored during config import | Immediate | Health | Local Manager raised NSX alarm lm_restore_while_config_import_in_progress. Silencieuse tant qu'un emplacement dont elle dépend est en panne | The location is in an inconsistent state. Delete the onboarding status of the location on the Global Manager, as the NSX alarm describes, then start the configuration import again. |
| NSX alarm: RTEP BGP neighbor down | Immediate | Health | Local Manager raised NSX alarm rtep_bgp_down. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| NSX alarm: RTEP connectivity lost | Immediate | Health | Local Manager raised NSX alarm rtep_connectivity_lost. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| NSX alarm: delta sync with the directory failed | Critical | Health | Local Manager raised NSX alarm error_in_delta_sync. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Look first for an alarm "connectivity to the LDAP server lost" on the same manager. Then read /var/log/syslog on the manager around the time of the alarm and search for "Error happened when synchronize LDAP objects". Ask the Active Directory administrator about recent changes in the domain named in the alarm. |
| NSX alarm: connectivity to the LDAP server lost | Critical | Health | Local Manager raised NSX alarm connectivity_to_ldap_server_lost. Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check that the LDAP server named in the alarm runs and is reachable from the Global Manager nodes, that no firewall blocks the path, and that its details are right in NSX. Then use Test Connection under Identity Firewall AD in the Global Manager UI. |

### Sync Channel

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Federation sync channel is down | Immediate | Health | Sync channel is down ("Connection up" inférieur à 1). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne ; silencieuse tant que son Global Manager ne répond pas | Check the connectivity between the two managers named on the channel (TCP 443 and 1236) and the state of both manager clusters. |

### Federation Edge Cluster

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Inter-site status of the edge cluster is not UP | Immediate | Health | Inter-site status of the edge cluster is not UP ("Inter-site status" différent de UP). Silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |

### Federation Edge Node

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Edge node has lost all inter-site BGP sessions | Immediate | Health | No inter-site BGP session of the edge node is established ("Inter-site BGP sessions established" inférieur à 1) et "Inter-site BGP sessions" au moins 1. Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |

### RTEP Tunnel

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| RTEP tunnel inter-site BGP session is down | Immediate | Health | Inter-site BGP session of the RTEP tunnel is not established ("Inter-site BGP established" inférieur à 1). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the edge clusters of the locations: RTEP VLAN, MTU (1700 recommended), routing between RTEP subnets and the state of the edge nodes. Stretched segments and gateways depend on these sessions. |
| RTEP tunnel is dropping packets | Immediate | Health | RTEP tunnel drops more than 10 received packets per minute ("Received packets dropped per minute" supérieur à 10) et RTEP tunnel drops more than 1% of the received packets ("Share of received packets dropped" supérieur à 1) ; ou RTEP tunnel drops more than 10 received packets per minute ("Received packets dropped per minute" supérieur à 10) et RTEP tunnel drops more than 5% of the received packets ("Share of received packets dropped" supérieur à 5) ; ou RTEP tunnel drops more than 10 sent packets per minute ("Sent packets dropped per minute" supérieur à 10) et RTEP tunnel drops more than 1% of the sent packets ("Share of sent packets dropped" supérieur à 1) ; ou RTEP tunnel drops more than 10 sent packets per minute ("Sent packets dropped per minute" supérieur à 10) et RTEP tunnel drops more than 5% of the sent packets ("Share of sent packets dropped" supérieur à 5). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the RTEP network between the two edge nodes of this tunnel: MTU on the whole path (1700 recommended), packet loss on the physical links and the load of the edge nodes. |
| RTEP tunnel inter-site BGP session is flapping | Warning | Health | Inter-site BGP session of the RTEP tunnel dropped since the previous cycle ("Inter-site BGP connection drops per hour" supérieur à 0) et Inter-site BGP session of the RTEP tunnel is established in this cycle ("Inter-site BGP established" supérieur à 0). Se déclenche après 1 cycle, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | The inter-site BGP session of this tunnel went down and came back. Check the RTEP path between the two edge nodes (physical links, WAN, MTU 1700 on the whole path) and the load of both edges around that time. Repeated flaps on every tunnel of an edge point to the edge or its uplinks; on one tunnel, to the path. |
| RTEP tunnel is blocking packets | Warning | Health | RTEP tunnel blocks more than 10 received packets per minute ("Received packets blocked per minute" supérieur à 10) ; ou RTEP tunnel blocks more than 10 sent packets per minute ("Sent packets blocked per minute" supérieur à 10). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Packets on this tunnel are blocked, not lost. Check the firewalls between the RTEP subnets of the two locations (Geneve, UDP 6081), the gateway firewall rules of the edge nodes, and any change made to them around the time the alert started. |
| RTEP tunnel traffic is above its usual range | Warning | Health | RTEP tunnel receive rate is above its usual range (seuil dynamique) ; ou RTEP tunnel send rate is above its usual range (seuil dynamique). Se déclenche après 3 cycles, s'efface après 3 | Inter-site traffic on this tunnel is above the range VCF Operations learned for it. Check whether a migration, a backup or a replication job explains it before looking for a loop or a misrouted flow. |

### Stretched Tier-0 Gateway

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Stretched Tier-0 gateway is in error at a location | Immediate | Health | Stretched Tier-0 gateway is in error at a location ("Locations in error" supérieur à 0). Silencieuse tant qu'un emplacement dont elle dépend est en panne | Open the object in the Global Manager and read the error for each location of its span. Fix the cause (a missing edge cluster, an address conflict, a location that is not reachable), then save the object again. |

### Stretched Tier-1 Gateway

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Stretched Tier-1 gateway is in error at a location | Immediate | Health | Stretched Tier-1 gateway is in error at a location ("Locations in error" supérieur à 0). Silencieuse tant qu'un emplacement dont elle dépend est en panne | Open the object in the Global Manager and read the error for each location of its span. Fix the cause (a missing edge cluster, an address conflict, a location that is not reachable), then save the object again. |

### Stretched Segment

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Stretched segment is in error at a location | Immediate | Health | Stretched segment is in error at a location ("Locations in error" supérieur à 0). Silencieuse tant qu'un emplacement dont elle dépend est en panne | Open the object in the Global Manager and read the error for each location of its span. Fix the cause (a missing edge cluster, an address conflict, a location that is not reachable), then save the object again. |

### Global Security Policy

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Global security policy is in error at a location | Immediate | Health | Global security policy is in error at a location ("Locations in error" supérieur à 0). Silencieuse tant qu'un emplacement dont elle dépend est en panne | Open the object in the Global Manager and read the error for each location of its span. Fix the cause (a missing edge cluster, an address conflict, a location that is not reachable), then save the object again. |

### Stretched Object at Location

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Stretched object is not realized at a location | Warning | Health | Stretched object is not realized at this location ("Realized" inférieur à 1). Se déclenche après 2 cycles, s'efface après 1 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Open the object in the Global Manager and read the realization status for this location. Check the location alarms and the connection between the Global Manager and the location, then save the object again if needed. |

### Tier-0 BGP Neighbor

| Alerte | Criticité maximale | Badge | Se déclenche quand | Recommandation |
| --- | --- | --- | --- | --- |
| Tier-0 BGP neighbor of a stretched gateway is down | Immediate | Health | BGP neighbor is not established ("Established" inférieur à 1). Se déclenche après 2 cycles, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | Check the BGP neighbor on the Tier-0 gateway: interface state of the edge uplink, the peer router, AS numbers, passwords and timers. |
| Tier-0 BGP neighbor of a stretched gateway is flapping | Warning | Health | BGP session dropped since the previous cycle ("Connection drops per hour" supérieur à 0) et BGP neighbor is established in this cycle ("Established" supérieur à 0). Se déclenche après 1 cycle, s'efface après 2 ; silencieuse tant qu'un emplacement dont elle dépend est en panne | The session went down and came back. Check the edge uplink and the peer router logs for interface flaps, hold-timer expiries or BFD events around that time. Repeated flaps usually point to a physical link, an MTU mismatch or a peer under load. |
| Tier-0 uplink BGP neighbor sends no prefixes | Warning | Health | No prefixes are received from the BGP neighbor ("Prefixes received" inférieur à 1) et "Established" au moins 1 et "Session type (uplink or inter-SR)" vaut uplink. Se déclenche après 2 cycles, s'efface après 2 | The session is established but the peer advertises nothing. Check the routing policy and redistribution on the peer router. |

## Métriques et propriétés

### NSX Federation

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Global Managers | Global Managers configured | `global_managers\|global_managers_configured` |  |
| Global Managers | Global Managers reachable | `global_managers\|global_managers_reachable` |  |
| Global Managers | Global Managers not reachable | `global_managers\|global_managers_unreachable` |  |
| Global Managers | Global Managers claiming active | `global_managers\|global_managers_active` | oui |
| Locations | Locations | `locations\|locations_total` |  |
| Locations | Locations connected to the Global Manager | `locations\|locations_gm_connected` |  |
| Locations | Locations reached by the collector | `locations\|locations_collected` |  |
| Stretched objects | Stretched objects | `stretched\|stretched_objects_total` |  |
| Stretched objects | Stretched objects realized at every location | `stretched\|stretched_objects_fully_realized` |  |
| Collection | Collection duration | `collection\|collection_duration_s` |  |
| Collection | NSX API calls in this cycle | `collection\|api_calls` |  |
| Collection | NSX API calls throttled | `collection\|api_calls_throttled` |  |
| Collection | NSX API errors | `collection\|api_errors` |  |
| Collection | NSX API calls without an answer (data unknown) | `collection\|data_gaps` |  |
| Collection | NSX API calls retried after a timeout | `collection\|api_calls_retried` |  |
| Global Managers | Listed Global Managers of another federation | `global_managers\|global_managers_foreign` |  |
| Locations | Locations not connected to the Global Manager | `locations\|locations_down` |  |
| Global Managers | Split-brain reported by NSX (1 = yes) | `global_managers\|split_brain_reported` |  |
| Collection | Stretched objects whose realization was read in this cycle | `collection\|realization_checked` |  |
| Collection | Objects deleted because they no longer exist in NSX | `collection\|stale_objects_deleted` |  |
| Collection | Managers that used their call budget | `collection\|managers_over_budget` |  |
| Locations | Locations healthy | `locations\|locations_healthy` |  |
| Locations | Share of locations healthy | `locations\|locations_healthy_pct` | oui |
| Inter-site | RTEP tunnels | `intersite\|rtep_tunnels_total` |  |
| Inter-site | RTEP tunnels with inter-site BGP down | `intersite\|rtep_tunnels_bgp_down` |  |
| Inter-site | RTEP packets dropped per minute, all tunnels | `intersite\|rtep_dropped_per_min` |  |
| Certificates | Days until the first manager certificate expires | `certificates\|cert_min_days_to_expiry` |  |
| Alarms | Open critical alarms, all managers | `alarms\|alarms_open_critical` |  |
|  | A standby Global Manager is ready to take over (1 = yes) | `switchover_ready` | oui |

Propriétés : Federation ID (`federation_id`) ; Active Global Manager (`active_global_manager`) ; Standby Global Manager configured (`standby_configured`) ; Collection mode (`collection_mode`) ; NSX API calls without an answer (`failed_calls`) ; Global Managers not collected (`global_manager_errors`) ; Locations in outage (`location_outage`) ; Federation configuration version (`config_version`) ; Global Manager addresses of the account (`global_manager_addresses`) ; VCF Operations API from the adapter (`vcf_operations_api`) ; Objects deleted because they no longer exist in NSX (`stale_objects`) ; Switchover readiness (`switchover_readiness`).

### Global Manager

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Connectivity | Reachable by the collector | `connectivity\|reachable` |  |
| Cluster | Nodes online | `cluster\|nodes_online` |  |
| Cluster | Nodes offline | `cluster\|nodes_offline` | oui |
| Cluster | Service groups not stable | `cluster\|service_groups_degraded` |  |
| Alarms | Open alarms: critical | `alarms\|alarms_open_critical` |  |
| Alarms | Open alarms: high | `alarms\|alarms_open_high` |  |
| Alarms | Open alarms: medium and low | `alarms\|alarms_open_other` |  |
| Backup | Hours since last successful backup | `backup\|backup_age_hours` |  |
| Certificates | Days until the first certificate expires | `certificates\|cert_min_days_to_expiry` |  |
| Sync | Standby sync entries reported | `sync\|standby_sync_entries` |  |
| Sync | Standby sync healthy (1 = in sync) | `sync\|standby_in_sync` | oui |
| Sync | Entries left to send to the standby | `sync\|standby_sync_remaining` |  |
| Sync | Standby out of sync for | `sync\|standby_out_of_sync_minutes` |  |
| Sync | Time since the last full sync to the standby | `sync\|standby_full_sync_age_hours` |  |
|  | Ready to take over as the active Global Manager (1 = yes) | `switchover_ready` |  |

Propriétés : Mode (`mode`) ; Cluster status (`cluster_status`) ; Version (`version`) ; Address used by the collector (`address`) ; Backup enabled (`backup_enabled`) ; Last backup result (`backup_last_result`) ; First certificate to expire (`cert_first_to_expire`) ; Switchover status (`switchover_status`) ; Standby sync status (`standby_sync_status`) ; Standby data consistent (as NSX reports it) (`standby_data_consistent`) ; Same version as the active Global Manager (`version_matches_active_gm`) ; Upgrade status (`upgrade_status`) ; Upgrade target version (`upgrade_target_version`) ; Switchover readiness (`switchover_readiness`) ; Certificate trust (`certificate_trust`) ; Locations in outage (`location_outage`).

### Manager Node

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
|  | Online | `online` |  |

Propriétés : Address (`address`) ; Node UUID (`uuid`).

### Location

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
|  | Location healthy (1 = yes) | `healthy` | oui |
| Connectivity | Connected to the active Global Manager | `connectivity\|gm_connection_up` | oui |
| Connectivity | Reached by the collector | `connectivity\|collector_reachable` |  |
| Cluster | Local Manager nodes online | `cluster\|nodes_online` |  |
| Cluster | Local Manager nodes offline | `cluster\|nodes_offline` |  |
| Alarms | Open alarms: critical | `alarms\|alarms_open_critical` |  |
| Alarms | Open alarms: high | `alarms\|alarms_open_high` |  |
| Alarms | Open alarms: medium and low | `alarms\|alarms_open_other` |  |
| Backup | Hours since last successful backup | `backup\|backup_age_hours` |  |
| Certificates | Days until the first certificate expires | `certificates\|cert_min_days_to_expiry` |  |
| Sync | Full syncs not completed | `sync\|full_sync_failed` | oui |
| Inter-site | Inter-site BGP sessions | `intersite\|intersite_bgp_total` |  |
| Inter-site | Inter-site BGP sessions established | `intersite\|intersite_bgp_established` |  |
| Inter-site | Inter-site BGP sessions not established | `intersite\|intersite_bgp_down` | oui |
| Collection | NSX API calls in this cycle | `collection\|api_calls` |  |
| Sync | Duration of the last full sync | `sync\|full_sync_duration_s` |  |
| Sync | Time since the last full sync | `sync\|full_sync_age_hours` |  |
| Connectivity | Time since the last successful collection | `connectivity\|minutes_since_collection` |  |
| Sync | Replication flows with the Global Manager | `sync\|replication_flows` |  |
| Sync | Replication flows in an error state | `sync\|replication_flows_unhealthy` |  |
| Connectivity | Round-trip time to the Global Manager | `connectivity\|gm_rtt_ms` |  |
| Connectivity | Round-trip time to the Global Manager, % of the maximum RTT setting | `connectivity\|gm_rtt_pct_of_max` |  |
| Sync | Sync queue used, fullest queue with the Global Manager | `sync\|sync_queue_used_pct` |  |
| Connectivity | Time since the Global Manager last saw the location | `connectivity\|gm_last_seen_minutes` |  |

Propriétés : Site ID (`site_id`) ; Version (`version`) ; Same version as the Global Manager (`version_matches_gm`) ; Local Manager address (`local_manager_address`) ; RTEP addresses (`rtep_ips`) ; Maximum RTT setting (ms) (`maximum_rtt_ms`) ; Local Manager cluster status (`cluster_status`) ; Last full sync stage (`full_sync_stage`) ; Backup enabled (`backup_enabled`) ; Last backup result (`backup_last_result`) ; First certificate to expire (`cert_first_to_expire`) ; Certificate trust (`certificate_trust`) ; Collector error (`collector_error`) ; NSX release newer than the Global Manager (`release_newer_than_gm`) ; Upgrade status (`upgrade_status`) ; Upgrade target version (`upgrade_target_version`) ; Federation alarm alerts raised by (`alarm_alerts_by`) ; Replication flows (`replication_state`) ; Onboarding status (`onboarding_status`) ; Site path (`site_path`) ; Local Manager certificate thumbprint (from the Global Manager) (`lm_thumbprint`) ; Locations in outage (`location_outage`).

### Sync Channel

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
|  | Connection up | `connection_up` | oui |

Propriétés : Source (`source`) ; Target (`target`) ; Target address (`target_address`) ; Locations in outage (`location_outage`) ; Global Manager outage (`gm_outage`).

### Federation Edge Cluster

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Inter-site | Inter-site BGP sessions | `intersite\|intersite_bgp_total` |  |
| Inter-site | Inter-site BGP sessions established | `intersite\|intersite_bgp_established` |  |
| Inter-site | Inter-site BGP sessions not established | `intersite\|intersite_bgp_down` |  |
|  | Edge nodes | `members` |  |

Propriétés : Inter-site status (`intersite_status`) ; RTEP addresses (`rtep_ips`) ; Inter-site forwarding enabled (`intersite_forwarding`) ; Location (`location`) ; Locations in outage (`location_outage`).

### Federation Edge Node

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Inter-site | Inter-site BGP sessions | `intersite\|intersite_bgp_total` |  |
| Inter-site | Inter-site BGP sessions established | `intersite\|intersite_bgp_established` | oui |
| Inter-site | Inter-site BGP sessions not established | `intersite\|intersite_bgp_down` |  |
|  | RTEP tunnels | `rtep_tunnels` |  |

Propriétés : Location (`location`) ; Locations in outage (`location_outage`).

### RTEP Tunnel

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Counters since start | Received bytes | `totals\|rx_bytes` |  |
| Counters since start | Sent bytes | `totals\|tx_bytes` |  |
| Counters since start | Received packets | `totals\|rx_packets` |  |
| Counters since start | Sent packets | `totals\|tx_packets` |  |
| Counters since start | Received packets dropped | `totals\|rx_dropped` |  |
| Counters since start | Sent packets dropped | `totals\|tx_dropped` |  |
| Counters since start | Received packets blocked | `totals\|rx_blocked` |  |
| Counters since start | Sent packets blocked | `totals\|tx_blocked` |  |
| Rates | Received bytes per second | `rates\|rx_bytes_per_s` |  |
| Rates | Sent bytes per second | `rates\|tx_bytes_per_s` |  |
| Rates | Received packets per minute | `rates\|rx_packets_per_min` |  |
| Rates | Sent packets per minute | `rates\|tx_packets_per_min` |  |
| Rates | Received packets dropped per minute | `rates\|rx_dropped_per_min` | oui |
| Rates | Sent packets dropped per minute | `rates\|tx_dropped_per_min` | oui |
| Rates | Received packets blocked per minute | `rates\|rx_blocked_per_min` |  |
| Rates | Sent packets blocked per minute | `rates\|tx_blocked_per_min` |  |
| Rates | Share of received packets dropped | `rates\|rx_drop_pct` | oui |
| Rates | Share of sent packets dropped | `rates\|tx_drop_pct` | oui |
|  | Inter-site BGP established | `bgp_established` | oui |
|  | Inter-site BGP connection drops | `connection_drops` |  |
|  | Inter-site BGP connection drops per hour | `connection_drops_per_hour` |  |

Propriétés : Local RTEP (`local_rtep`) ; Remote RTEP (`remote_rtep`) ; Remote location (`remote_location`) ; Inter-site BGP state (`bgp_state`) ; Locations in outage (`location_outage`).

### Stretched Tier-0 Gateway

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Realization | Locations in span | `realization\|span_locations` |  |
| Realization | Locations realized | `realization\|realized_locations` |  |
| Realization | Locations not realized | `realization\|unrealized_locations` |  |
| Realization | Locations in error | `realization\|error_locations` | oui |

Propriétés : HA mode (`ha_mode`) ; Primary location (`primary_location`) ; Consolidated status (`status`) ; Policy path (`path`) ; Revision (`revision`) ; Last modified by (`last_modified_user`) ; Last modified (`last_modified_time`) ; Locations in outage (`location_outage`).

### Stretched Tier-1 Gateway

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Realization | Locations in span | `realization\|span_locations` |  |
| Realization | Locations realized | `realization\|realized_locations` |  |
| Realization | Locations not realized | `realization\|unrealized_locations` |  |
| Realization | Locations in error | `realization\|error_locations` | oui |

Propriétés : Connected Tier-0 (`tier0`) ; Primary location (`primary_location`) ; Consolidated status (`status`) ; Policy path (`path`) ; Revision (`revision`) ; Last modified by (`last_modified_user`) ; Last modified (`last_modified_time`) ; Locations in outage (`location_outage`).

### Stretched Segment

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Realization | Locations in span | `realization\|span_locations` |  |
| Realization | Locations realized | `realization\|realized_locations` |  |
| Realization | Locations not realized | `realization\|unrealized_locations` |  |
| Realization | Locations in error | `realization\|error_locations` | oui |

Propriétés : Subnets (`subnets`) ; Connected gateway (`gateway`) ; Type (`segment_type`) ; Consolidated status (`status`) ; Policy path (`path`) ; Revision (`revision`) ; Last modified by (`last_modified_user`) ; Last modified (`last_modified_time`) ; Locations in outage (`location_outage`).

### Global Security Policy

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Rules | Rules | `rules\|rules_total` |  |
| Rules | Rules disabled | `rules\|rules_disabled` |  |
| Traffic | Rule hits (all locations) | `traffic\|hit_count` |  |
| Traffic | Rule hits per minute (all locations) | `traffic\|hit_count_per_min` |  |
| Realization | Locations in span | `realization\|span_locations` |  |
| Realization | Locations realized | `realization\|realized_locations` |  |
| Realization | Locations not realized | `realization\|unrealized_locations` |  |
| Realization | Locations in error | `realization\|error_locations` | oui |

Propriétés : Category (`category`) ; Domain (`domain`) ; Applied to (`scope`) ; Consolidated status (`status`) ; Policy path (`path`) ; Revision (`revision`) ; Last modified by (`last_modified_user`) ; Last modified (`last_modified_time`) ; Locations in outage (`location_outage`).

### Stretched Object at Location

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
|  | Realized | `realized` | oui |
| Traffic | Rule hits | `traffic\|hit_count` |  |
| Traffic | Rule hits per minute | `traffic\|hit_count_per_min` |  |
| Traffic | Packets | `traffic\|packet_count` |  |
| Traffic | Bytes | `traffic\|byte_count` |  |
| Traffic | Sessions | `traffic\|session_count` |  |

Propriétés : Realization status (`status`) ; Location (`location`) ; Object path (`object_path`) ; Object type (`object_type`) ; Locations in outage (`location_outage`).

### Tier-0 BGP Neighbor

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
|  | Established | `established` | oui |
| Prefixes and messages | Prefixes received | `prefixes\|prefixes_in` |  |
| Prefixes and messages | Prefixes advertised | `prefixes\|prefixes_out` |  |
| Prefixes and messages | Messages received | `prefixes\|messages_in` |  |
| Prefixes and messages | Messages sent | `prefixes\|messages_out` |  |
|  | Seconds established | `seconds_established` |  |
|  | Connection drops | `connection_drops` |  |
|  | Connection drops per hour | `connection_drops_per_hour` |  |

Propriétés : Connection state (`state`) ; Remote AS (`remote_as`) ; Source address (`source_address`) ; Neighbor address (`neighbor_address`) ; Location (`location`) ; Edge (`edge`) ; Session type (uplink or inter-SR) (`session_type`) ; Locations in outage (`location_outage`).

### Global Firewall Rule

| Groupe | Métrique | Clé | Indicateur clé |
| --- | --- | --- | --- |
| Traffic | Hits (all locations) | `traffic\|hit_count` |  |
| Traffic | Hits per minute (all locations) | `traffic\|hit_count_per_min` |  |
| Traffic | Packets | `traffic\|packet_count` |  |
| Traffic | Bytes | `traffic\|byte_count` |  |
| Traffic | Sessions | `traffic\|session_count` |  |

Propriétés : Action (`action`) ; Disabled (`disabled`) ; Sources (`sources`) ; Destinations (`destinations`).

## Tableaux de bord, vues, rapport et groupes

- Tableaux de bord (dossier « NSX Federation ») : Federation Overview, Location Matrix, Switchover Readiness, Inter-site Connectivity, Stretched Objects.
- Vues en liste : NSX Federation - Locations ; NSX Federation - Sync channels ; NSX Federation - RTEP tunnels ; NSX Federation - Tier-0 BGP neighbors ; NSX Federation - Realization per location.
- Rapport : NSX Federation - Health report.
- Groupes personnalisés : NSX Federation - All objects ; NSX Federation - Managers and locations ; NSX Federation - Inter-site connectivity ; NSX Federation - Stretched objects.
- Arborescence d'inventaire : NSX Federation (Inventory, Detailed View, dernier onglet).
