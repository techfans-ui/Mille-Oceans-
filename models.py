"""
Mille Oceans — Modèles de données pour la gestion des stocks de boissons.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Utilisateurs
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    """Comptes utilisateurs."""
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='staff')  # admin, manager, staff
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.prenom} {self.nom}"

    def is_admin(self):
        return self.role == 'admin'

    def is_manager(self):
        return self.role in ('admin', 'manager')


# ---------------------------------------------------------------------------
# Catégories de boissons
# ---------------------------------------------------------------------------

class Categorie(db.Model):
    """Catégories de boissons (Bières, Vins, Spiritueux, Jus, Eaux…)."""
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    icone = db.Column(db.String(50), default='bi-cup-straw')
    couleur = db.Column(db.String(20), default='primary')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    produits = db.relationship('Produit', backref='categorie', lazy=True)

    @property
    def nb_produits(self):
        return len(self.produits)

    @property
    def stock_total(self):
        return sum(p.quantite_stock for p in self.produits)


# ---------------------------------------------------------------------------
# Fournisseurs
# ---------------------------------------------------------------------------

class Fournisseur(db.Model):
    """Fournisseurs de boissons."""
    __tablename__ = 'fournisseurs'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    contact = db.Column(db.String(100))
    telephone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    adresse = db.Column(db.Text)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    produits = db.relationship('Produit', backref='fournisseur', lazy=True)


# ---------------------------------------------------------------------------
# Produits (boissons)
# ---------------------------------------------------------------------------

class Produit(db.Model):
    """Produit / boisson en stock."""
    __tablename__ = 'produits'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    code_barre = db.Column(db.String(50), unique=True)
    description = db.Column(db.Text)
    categorie_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    fournisseur_id = db.Column(db.Integer, db.ForeignKey('fournisseurs.id'))

    # Stock
    quantite_stock = db.Column(db.Integer, default=0)
    stock_minimum = db.Column(db.Integer, default=10)
    stock_maximum = db.Column(db.Integer, default=200)
    unite = db.Column(db.String(30), default='unité')  # unité, carton, caisse, pack, litre

    # Prix
    prix_achat = db.Column(db.Float, default=0.0)
    prix_vente = db.Column(db.Float, default=0.0)

    # Détails
    volume = db.Column(db.String(30))     # ex: 33cl, 75cl, 1L
    marque = db.Column(db.String(100))
    pays_origine = db.Column(db.String(60))
    degre_alcool = db.Column(db.Float, default=0.0)

    is_active = db.Column(db.Boolean, default=True)
    image_url = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    mouvements = db.relationship('MouvementStock', backref='produit', lazy=True,
                                 order_by='MouvementStock.date_mouvement.desc()')

    @property
    def valeur_stock(self):
        return self.quantite_stock * self.prix_achat

    @property
    def marge_unitaire(self):
        return self.prix_vente - self.prix_achat

    @property
    def est_stock_bas(self):
        return self.quantite_stock <= self.stock_minimum

    @property
    def est_stock_critique(self):
        return self.quantite_stock <= 5

    @property
    def est_rupture(self):
        return self.quantite_stock <= 0

    @property
    def niveau_stock_pct(self):
        if self.stock_maximum <= 0:
            return 0
        return min(100, int(self.quantite_stock / self.stock_maximum * 100))


# ---------------------------------------------------------------------------
# Mouvements de stock
# ---------------------------------------------------------------------------

class MouvementStock(db.Model):
    """Entrées, sorties et ajustements de stock."""
    __tablename__ = 'mouvements_stock'
    id = db.Column(db.Integer, primary_key=True)
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'), nullable=False)
    type_mouvement = db.Column(db.String(20), nullable=False)  # entree, sortie, ajustement, perte
    quantite = db.Column(db.Integer, nullable=False)
    quantite_avant = db.Column(db.Integer, default=0)
    quantite_apres = db.Column(db.Integer, default=0)
    motif = db.Column(db.String(200))
    reference = db.Column(db.String(100))     # N° bon de livraison, facture…
    prix_unitaire = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date_mouvement = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    notes = db.Column(db.Text)

    user = db.relationship('User', backref='mouvements')

    @property
    def montant_total(self):
        return self.quantite * self.prix_unitaire

    @property
    def type_label(self):
        labels = {
            'entree': 'Entrée',
            'sortie': 'Sortie',
            'ajustement': 'Ajustement',
            'perte': 'Perte'
        }
        return labels.get(self.type_mouvement, self.type_mouvement)

    @property
    def type_couleur(self):
        couleurs = {
            'entree': 'success',
            'sortie': 'warning',
            'ajustement': 'info',
            'perte': 'danger'
        }
        return couleurs.get(self.type_mouvement, 'secondary')


# ---------------------------------------------------------------------------
# Alertes
# ---------------------------------------------------------------------------

class Alerte(db.Model):
    """Alertes de stock."""
    __tablename__ = 'alertes'
    id = db.Column(db.Integer, primary_key=True)
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'), nullable=False)
    type_alerte = db.Column(db.String(30), nullable=False)  # stock_bas, rupture, surstock
    message = db.Column(db.String(300), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    is_resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    produit = db.relationship('Produit', backref='alertes')

    @property
    def type_couleur(self):
        couleurs = {
            'stock_bas': 'warning',
            'rupture': 'danger',
            'surstock': 'info'
        }
        return couleurs.get(self.type_alerte, 'secondary')

    @property
    def type_icone(self):
        icones = {
            'stock_bas': 'bi-exclamation-triangle',
            'rupture': 'bi-x-octagon',
            'surstock': 'bi-arrow-up-circle'
        }
        return icones.get(self.type_alerte, 'bi-bell')
