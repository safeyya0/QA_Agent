# Spécification de Tests — Automation Exercise
# URL cible : https://automationexercise.com

---

## TC-001 : Connexion avec identifiants valides
Given: L'utilisateur est sur la page de connexion
When: Il saisit un email et un mot de passe valides et clique sur Login
Then: Il est redirigé vers la page d'accueil et son nom apparaît dans la barre de navigation
**Résultat global attendu:** Connexion réussie, nom d'utilisateur visible
**Données de test suggérées:** `Email: testqa001@gmail.com, Password: Test1234!`

---

## TC-002 : Connexion avec mot de passe incorrect
Given: L'utilisateur est sur la page de connexion
When: Il saisit un email valide et un mauvais mot de passe
Then: Un message d'erreur s'affiche
**Résultat global attendu:** Message d'erreur affiché, utilisateur non connecté
**Données de test suggérées:** `Email: testqa001@gmail.com, Password: wrongpass`

---

## TC-003 : Inscription d'un nouvel utilisateur
Given: L'utilisateur est sur la page de signup
When: Il remplit le formulaire avec un nom et un email unique puis clique sur Signup
Then: Il accède au formulaire de création de compte complet
**Résultat global attendu:** Formulaire d'inscription accessible
**Données de test suggérées:** `Name: QA Tester, Email: qatest_unique123@mailtest.com`

---

## TC-004 : Recherche d'un produit
Given: L'utilisateur est sur la page Products
When: Il saisit un nom de produit dans la barre de recherche et clique sur Search
Then: Les produits correspondants s'affichent
**Résultat global attendu:** Résultats de recherche affichés
**Données de test suggérées:** `Produit: dress`

---

## TC-005 : Ajout d'un produit au panier
Given: L'utilisateur est sur la page Products
When: Il clique sur Add to Cart pour un produit
Then: Le produit est ajouté au panier et une confirmation s'affiche
**Résultat global attendu:** Produit ajouté au panier avec succès

---

## TC-006 : Visualisation du panier
Given: L'utilisateur a ajouté un produit au panier
When: Il clique sur l'icône Cart dans la navigation
Then: La page du panier affiche le produit avec son prix et sa quantité
**Résultat global attendu:** Panier affiché avec le produit

---

## TC-007 : Suppression d'un produit du panier
Given: L'utilisateur a un produit dans son panier
When: Il clique sur le bouton de suppression (X) du produit
Then: Le produit est retiré du panier
**Résultat global attendu:** Panier vide après suppression

---

## TC-008 : Navigation vers la page Contact
Given: L'utilisateur est sur la page d'accueil
When: Il clique sur Contact Us dans la navigation
Then: Le formulaire de contact s'affiche
**Résultat global attendu:** Formulaire de contact visible

---

## TC-009 : Soumission du formulaire de contact
Given: L'utilisateur est sur la page Contact Us
When: Il remplit tous les champs et clique sur Submit
Then: Un message de confirmation s'affiche
**Résultat global attendu:** Message de succès affiché après soumission
**Données de test suggérées:** `Name: QA User, Email: qa@test.com, Subject: Test, Message: Ceci est un test automatisé`

---

## TC-010 : Accès à la liste des produits
Given: L'utilisateur est sur la page d'accueil
When: Il clique sur Products dans la navigation
Then: La liste complète des produits s'affiche avec leurs noms et prix
**Résultat global attendu:** Page Products affichée avec produits visibles

---

## TC-011 : Consultation du détail d'un produit
Given: L'utilisateur est sur la page Products
When: Il clique sur View Product pour un article
Then: La page de détail du produit s'affiche avec description, prix et catégorie
**Résultat global attendu:** Détails du produit visibles

---

## TC-012 : Déconnexion
Given: L'utilisateur est connecté
When: Il clique sur Logout dans la navigation
Then: Il est redirigé vers la page de connexion
**Résultat global attendu:** Déconnexion réussie, retour à la page Login
**Données de test suggérées:** `Email: testqa001@gmail.com, Password: Test1234!`

---

## TC-013 : Accès à la page Test Cases
Given: L'utilisateur est sur la page d'accueil
When: Il clique sur Test Cases dans la navigation
Then: La page listant les cas de test s'affiche
**Résultat global attendu:** Page Test Cases visible

---

## TC-014 : Soumission formulaire contact sans champs requis
Given: L'utilisateur est sur la page Contact Us
When: Il clique sur Submit sans remplir les champs obligatoires
Then: Un message d'erreur ou une validation s'affiche
**Résultat global attendu:** Formulaire non soumis, erreur affichée

---

## TC-015 : Scroll vers le bas et retour en haut
Given: L'utilisateur est sur la page d'accueil
When: Il fait défiler la page jusqu'en bas et clique sur le bouton de retour en haut
Then: La page revient en position haute
**Résultat global attendu:** Page remontée en haut
