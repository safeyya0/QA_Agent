# Spécification de Tests — OrangeHRM
# URL cible : https://opensource-demo.orangehrmlive.com/web/index.php/auth/login

---

## TC-001 : Recherche d'utilisateurs avec filtres valides
Given: L'utilisateur est connecté avec un rôle Admin
When: Il navigue vers Admin > User Management > Users, entre 'Admin' dans Username, sélectionne 'Admin' comme User Role et clique sur Search
Then: La liste des utilisateurs correspondants s'affiche
**Résultat global attendu:** Résultats de recherche affichés avec les utilisateurs Admin
**Données de test suggérées:** `Username: Admin, User Role: Admin`

---

## TC-002 : Ajout d'un nouvel utilisateur
Given: L'utilisateur est connecté avec un rôle Admin
When: Il navigue vers Admin > User Management > Users, clique sur Add, remplit Username, Employee Name, User Role, Password, Confirm Password et clique sur Save
Then: L'utilisateur est ajouté avec succès
**Résultat global attendu:** Message "Successfully Saved" affiché
**Données de test suggérées:** `Username: testuser_qa01, Employee Name: Paul Smith, User Role: ESS, Password: Admin1234!, Confirm Password: Admin1234!`

---

## TC-003 : Modification d'un utilisateur existant
Given: L'utilisateur est connecté avec un rôle Admin
When: Il navigue vers Admin > User Management > Users, recherche l'utilisateur 'testuser_qa01', clique sur l'icône d'édition, modifie le User Role et clique sur Save
Then: Les modifications sont enregistrées avec succès
**Résultat global attendu:** Message "Successfully Updated" affiché
**Données de test suggérées:** `Username: testuser_qa01`

---

## TC-004 : Suppression d'un utilisateur
Given: L'utilisateur est connecté avec un rôle Admin
When: Il navigue vers Admin > User Management > Users, recherche l'utilisateur 'testuser_qa01', coche sa case et clique sur Delete, puis confirme
Then: L'utilisateur est supprimé avec succès
**Résultat global attendu:** Message "Successfully Deleted" affiché
**Données de test suggérées:** `Username: testuser_qa01`

---

## TC-005 : Recherche avec un nom d'utilisateur inexistant
Given: L'utilisateur est connecté avec un rôle Admin
When: Il navigue vers Admin > User Management > Users, entre 'xxxxxxxxxnotexist' dans Username et clique sur Search
Then: Aucun résultat n'est affiché
**Résultat global attendu:** Message "No Records Found" affiché
**Données de test suggérées:** `Username: xxxxxxxxxnotexist`

---

## TC-006 : Vérification du nom d'utilisateur affiché après connexion
Given: L'utilisateur est connecté en tant qu'Admin
When: Il clique sur l'icône de profil en haut à droite
Then: Le menu déroulant affiche le nom de l'utilisateur connecté
**Résultat global attendu:** Menu profil visible avec option Logout
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-007 : Navigation vers le module Admin
Given: L'utilisateur est connecté
When: Il clique sur Admin dans la barre de navigation
Then: La page User Management s'affiche
**Résultat global attendu:** Page "User Management" visible
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-008 : Navigation vers le module PIM
Given: L'utilisateur est connecté
When: Il clique sur PIM dans la barre de navigation
Then: La page Employee List s'affiche
**Résultat global attendu:** Page "Employee List" visible
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-009 : Accès au formulaire de changement de mot de passe
Given: L'utilisateur est connecté
When: Il clique sur l'icône de profil en haut à droite puis sélectionne Change Password
Then: Le formulaire de changement de mot de passe s'affiche
**Résultat global attendu:** Formulaire "Change Password" visible avec les champs Current Password, New Password et Confirm Password
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-010 : Recherche d'employés avec filtres valides
Given: L'utilisateur est connecté
When: Il navigue vers PIM > Employee List, entre 'John' dans Employee Name et clique sur Search
Then: La liste des employés correspondants s'affiche
**Résultat global attendu:** Résultats de recherche affichés avec des employés contenant 'John'
**Données de test suggérées:** `Employee Name: John`

---

## TC-011 : Ajout d'un nouvel employé
Given: L'utilisateur est connecté
When: Il navigue vers PIM > Employee List, clique sur Add, remplit First Name, Last Name, Employee Id et clique sur Save
Then: L'employé est ajouté avec succès
**Résultat global attendu:** Message "Successfully Saved" affiché
**Données de test suggérées:** `First Name: Jane, Last Name: Smith, Employee Id: EMP99901`

---

## TC-012 : Vérification du widget Time at Work sur le Dashboard
Given: L'utilisateur est connecté
When: Il accède au Dashboard
Then: Le widget Time at Work est visible
**Résultat global attendu:** Texte "Time at Work" affiché sur le Dashboard
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-013 : Vérification du widget My Actions sur le Dashboard
Given: L'utilisateur est connecté
When: Il accède au Dashboard
Then: Le widget My Actions est visible
**Résultat global attendu:** Texte "My Actions" affiché sur le Dashboard
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-014 : Utilisation du raccourci Quick Launch
Given: L'utilisateur est connecté et sur le Dashboard
When: Il clique sur 'View Employee List' dans le widget Quick Launch
Then: La page Employee List s'affiche
**Résultat global attendu:** Texte "Employee List" affiché
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-015 : Masquage de la barre latérale
Given: L'utilisateur est connecté et sur le Dashboard
When: Il clique sur le bouton fléché pour masquer la barre latérale
Then: La barre latérale est masquée et le Dashboard reste visible
**Résultat global attendu:** Dashboard toujours visible après masquage de la barre latérale
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-016 : Consultation des feuilles de temps d'un employé
Given: L'utilisateur est connecté
When: Il navigue vers Time > Project Timesheets, entre 'Admin' dans Employee Name et clique sur View
Then: Les feuilles de temps de l'employé s'affichent
**Résultat global attendu:** Page de feuilles de temps affichée
**Données de test suggérées:** `Employee Name: Admin`

---

## TC-017 : Tentative de consultation des feuilles de temps sans sélection d'employé
Given: L'utilisateur est connecté
When: Il navigue vers Time > Project Timesheets et clique sur View sans sélectionner d'employé
Then: Un message de validation s'affiche ou le formulaire n'est pas soumis
**Résultat global attendu:** Message "Required" affiché ou page inchangée
**Données de test suggérées:** `Champ Employee Name laissé vide`

---

## TC-018 : Recherche de candidats dans le module Recruitment
Given: L'utilisateur est connecté
When: Il navigue vers Recruitment > Candidates, entre 'Software' dans Keywords et clique sur Search
Then: La liste des candidats s'affiche
**Résultat global attendu:** Page "Candidates" affichée avec les résultats de recherche
**Données de test suggérées:** `Keywords: Software`

---

## TC-019 : Recherche de candidats sans correspondance
Given: L'utilisateur est connecté
When: Il navigue vers Recruitment > Candidates, entre 'zzznomatch99999' dans Keywords et clique sur Search
Then: Aucun candidat n'est affiché
**Résultat global attendu:** Message "No Records Found" affiché
**Données de test suggérées:** `Keywords: zzznomatch99999`

---

## TC-020 : Ajout d'un nouveau candidat
Given: L'utilisateur est connecté
When: Il navigue vers Recruitment > Candidates, clique sur Add, remplit First Name, Last Name et Email, sélectionne le premier Vacancy disponible et clique sur Save
Then: Le candidat est ajouté avec succès
**Résultat global attendu:** Message "Successfully Saved" affiché
**Données de test suggérées:** `First Name: Alice, Last Name: Dupont, Email: alice.dupont.qa@testmail.com`

---

## TC-021 : Modification des informations personnelles
Given: L'utilisateur est connecté
When: Il navigue vers My Info > Personal Details, modifie le champ Nickname avec la valeur 'QATester' et clique sur Save
Then: Les modifications sont enregistrées
**Résultat global attendu:** Message "Successfully Updated" affiché
**Données de test suggérées:** `Nickname: QATester`

---

## TC-022 : Tentative de sauvegarde avec un champ requis vide
Given: L'utilisateur est connecté
When: Il navigue vers My Info > Personal Details, efface le contenu du champ Last Name et clique sur Save
Then: Un message de validation s'affiche
**Résultat global attendu:** Message "Required" affiché
**Données de test suggérées:** `Last Name: (vide)`

---

## TC-023 : Consultation de l'onglet Attachments dans My Info
Given: L'utilisateur est connecté
When: Il navigue vers My Info puis clique sur l'onglet Attachments
Then: L'onglet Attachments s'affiche avec le bouton Add
**Résultat global attendu:** Texte "Attachments" visible et bouton Add présent
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-024 : Consultation des évaluations dans Performance
Given: L'utilisateur est connecté
When: Il navigue vers Performance > Manage Reviews et clique sur Search
Then: La liste des évaluations s'affiche
**Résultat global attendu:** Page "Manage Reviews" affichée avec les résultats
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-025 : Recherche sans résultats dans Performance
Given: L'utilisateur est connecté
When: Il navigue vers Performance > Manage Reviews, entre 'zzznomatch99999' dans Employee Name et clique sur Search
Then: Aucun résultat n'est affiché
**Résultat global attendu:** Message "No Records Found" affiché
**Données de test suggérées:** `Employee Name: zzznomatch99999`

---

## TC-026 : Recherche d'un employé dans l'annuaire
Given: L'utilisateur est connecté
When: Il navigue vers Directory, entre 'John' dans Employee Name et clique sur Search
Then: Les fiches des employés correspondants s'affichent
**Résultat global attendu:** Résultats affichés dans l'annuaire avec des employés contenant 'John'
**Données de test suggérées:** `Employee Name: John`

---

## TC-027 : Accès au module Claim
Given: L'utilisateur est connecté
When: Il navigue vers Claim > My Claims
Then: La page My Claims s'affiche
**Résultat global attendu:** Texte "My Claims" visible
**Données de test suggérées:** `Username: Admin, Password: admin123`

---

## TC-028 : Tentative de soumission d'une note de frais sans Event Name
Given: L'utilisateur est connecté
When: Il navigue vers Claim > Submit Claim, laisse le champ Event Name vide et clique sur Create
Then: Un message de validation s'affiche
**Résultat global attendu:** Message "Required" affiché
**Données de test suggérées:** `Event Name: (vide)`

---

## TC-029 : Publication d'un message dans le flux Buzz
Given: L'utilisateur est connecté
When: Il navigue vers Buzz, saisit 'Hello QA Test' dans le champ What's on your mind? et clique sur Post
Then: Le message est publié et apparaît dans le flux
**Résultat global attendu:** Texte "Hello QA Test" visible dans le flux Buzz
**Données de test suggérées:** `Message: Hello QA Test`

---

## TC-030 : Tentative de publication d'un message vide dans Buzz
Given: L'utilisateur est connecté et sur la page Buzz
When: Il navigue vers Buzz et tente de cliquer sur le bouton Post sans saisir de message
Then: Le message n'est pas publié et la page reste inchangée
**Résultat global attendu:** Le bouton Post est désactivé ou la page reste sur Buzz sans nouveau post
**Données de test suggérées:** `Champ message laissé vide`
