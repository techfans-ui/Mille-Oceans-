"""
Mille Oceans — Application de gestion des stocks de boissons.
Point d'entrée Flask avec toutes les routes.
"""

import os
import csv
import io
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, abort, Response, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user,
                          login_required, current_user)
from sqlalchemy import func, desc

from config import DevelopmentConfig, ProductionConfig
from models import (db, User, Categorie, Fournisseur, Produit,
                    MouvementStock, Alerte)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config_class=None):
    if config_class is None:
        config_class = ProductionConfig if os.environ.get('RENDER') else DevelopmentConfig
    app = Flask(__name__)
    app.config.from_object(config_class)
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)

    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'
    login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ------------------------------------------------------------------
    # Décorateurs
    # ------------------------------------------------------------------

    def manager_required(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not current_user.is_manager():
                flash("Accès réservé aux managers et administrateurs.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated

    def admin_required(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not current_user.is_admin():
                flash("Accès réservé aux administrateurs.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template('500.html'), 500

    # ------------------------------------------------------------------
    # PWA — serve manifest and service worker from root
    # ------------------------------------------------------------------

    @app.route('/manifest.json')
    def pwa_manifest():
        return send_from_directory(app.static_folder, 'manifest.json',
                                   mimetype='application/manifest+json')

    @app.route('/sw.js')
    def pwa_sw():
        response = send_from_directory(app.static_folder, 'sw.js',
                                        mimetype='application/javascript')
        response.headers['Service-Worker-Allowed'] = '/'
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.route('/offline')
    def offline():
        return render_template('offline.html')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def verifier_alertes(produit):
        """Crée ou résout les alertes pour un produit."""
        # Résoudre les anciennes alertes
        Alerte.query.filter_by(produit_id=produit.id, is_resolved=False).update(
            {'is_resolved': True})

        if produit.est_rupture:
            alerte = Alerte(
                produit_id=produit.id,
                type_alerte='rupture',
                message=f"RUPTURE DE STOCK : {produit.nom} — Quantité : {produit.quantite_stock}"
            )
            db.session.add(alerte)
        elif produit.est_stock_bas:
            alerte = Alerte(
                produit_id=produit.id,
                type_alerte='stock_bas',
                message=f"Stock bas : {produit.nom} — {produit.quantite_stock} {produit.unite}(s) restant(s)"
            )
            db.session.add(alerte)
        elif produit.quantite_stock > produit.stock_maximum:
            alerte = Alerte(
                produit_id=produit.id,
                type_alerte='surstock',
                message=f"Surstock : {produit.nom} — {produit.quantite_stock}/{produit.stock_maximum}"
            )
            db.session.add(alerte)

    def enregistrer_mouvement(produit, type_mv, quantite, motif='',
                               reference='', prix_unit=0, notes=''):
        """Enregistre un mouvement de stock et met à jour la quantité."""
        avant = produit.quantite_stock

        if type_mv == 'entree':
            produit.quantite_stock += quantite
        elif type_mv in ('sortie', 'perte'):
            produit.quantite_stock = max(0, produit.quantite_stock - quantite)
        elif type_mv == 'ajustement':
            produit.quantite_stock = quantite  # valeur absolue

        mv = MouvementStock(
            produit_id=produit.id,
            type_mouvement=type_mv,
            quantite=quantite,
            quantite_avant=avant,
            quantite_apres=produit.quantite_stock,
            motif=motif,
            reference=reference,
            prix_unitaire=prix_unit,
            user_id=current_user.id,
            notes=notes
        )
        db.session.add(mv)
        verifier_alertes(produit)
        return mv

    # ------------------------------------------------------------------
    # Context processors
    # ------------------------------------------------------------------

    @app.context_processor
    def inject_globals():
        alertes_count = 0
        if current_user.is_authenticated:
            alertes_count = Alerte.query.filter_by(is_read=False, is_resolved=False).count()
        return dict(alertes_non_lues=alertes_count, app_name='Mille Oceans')

    # ==================================================================
    # ROUTES — Authentification
    # ==================================================================

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user)
                flash(f'Bienvenue, {user.prenom} !', 'success')
                return redirect(request.args.get('next') or url_for('dashboard'))
            flash('Email ou mot de passe incorrect.', 'danger')
        return render_template('login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        if request.method == 'POST':
            nom = request.form.get('nom', '').strip()
            prenom = request.form.get('prenom', '').strip()
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')

            if User.query.filter_by(email=email).first():
                flash('Cet email est déjà utilisé.', 'danger')
                return redirect(url_for('register'))
            if User.query.filter_by(username=username).first():
                flash("Ce nom d'utilisateur est déjà pris.", 'danger')
                return redirect(url_for('register'))

            # Premier utilisateur = admin
            role = 'admin' if User.query.count() == 0 else 'staff'
            user = User(nom=nom, prenom=prenom, username=username,
                        email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash(f'Compte créé ! Bienvenue, {prenom}.', 'success')
            return redirect(url_for('dashboard'))
        return render_template('register.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Déconnecté.', 'info')
        return redirect(url_for('login'))

    # ==================================================================
    # ROUTES — Profil
    # ==================================================================

    @app.route('/profil', methods=['GET', 'POST'])
    @login_required
    def profil():
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'info':
                current_user.nom = request.form.get('nom', '').strip()
                current_user.prenom = request.form.get('prenom', '').strip()
                new_email = request.form.get('email', '').strip().lower()
                if new_email != current_user.email:
                    if User.query.filter_by(email=new_email).first():
                        flash('Cet email est déjà utilisé.', 'danger')
                        return redirect(url_for('profil'))
                    current_user.email = new_email
                db.session.commit()
                flash('Informations mises à jour.', 'success')
            elif action == 'password':
                current_pw = request.form.get('current_password', '')
                new_pw = request.form.get('new_password', '')
                confirm_pw = request.form.get('confirm_password', '')
                if not current_user.check_password(current_pw):
                    flash('Mot de passe actuel incorrect.', 'danger')
                elif len(new_pw) < 4:
                    flash('Le nouveau mot de passe doit contenir au moins 4 caractères.', 'danger')
                elif new_pw != confirm_pw:
                    flash('Les mots de passe ne correspondent pas.', 'danger')
                else:
                    current_user.set_password(new_pw)
                    db.session.commit()
                    flash('Mot de passe changé avec succès.', 'success')
            return redirect(url_for('profil'))

        # Stats de l'utilisateur
        nb_mouvements = MouvementStock.query.filter_by(user_id=current_user.id).count()
        dernier_mouvement = MouvementStock.query.filter_by(user_id=current_user.id)\
            .order_by(MouvementStock.date_mouvement.desc()).first()
        return render_template('profil.html',
                               nb_mouvements=nb_mouvements,
                               dernier_mouvement=dernier_mouvement)

    # ==================================================================
    # ROUTES — Tableau de bord
    # ==================================================================

    @app.route('/')
    @app.route('/dashboard')
    @login_required
    def dashboard():
        # Statistiques générales
        total_produits = Produit.query.filter_by(is_active=True).count()
        total_categories = Categorie.query.count()
        total_fournisseurs = Fournisseur.query.filter_by(is_active=True).count()

        # Valeur totale du stock
        produits = Produit.query.filter_by(is_active=True).all()
        valeur_totale = sum(p.valeur_stock for p in produits)
        valeur_vente = sum(p.quantite_stock * p.prix_vente for p in produits)

        # Alertes
        produits_rupture = [p for p in produits if p.est_rupture]
        produits_stock_bas = [p for p in produits if p.est_stock_bas and not p.est_rupture]
        alertes_actives = Alerte.query.filter_by(is_resolved=False).order_by(
            Alerte.created_at.desc()).limit(10).all()

        # Mouvements récents
        mouvements_recents = (MouvementStock.query
                              .order_by(MouvementStock.date_mouvement.desc())
                              .limit(10).all())

        # Données graphique — mouvements des 7 derniers jours
        today = datetime.now(timezone.utc).date()
        chart_data = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            entrees = db.session.query(func.coalesce(func.sum(MouvementStock.quantite), 0)).filter(
                MouvementStock.type_mouvement == 'entree',
                MouvementStock.date_mouvement >= day_start,
                MouvementStock.date_mouvement < day_end
            ).scalar()
            sorties = db.session.query(func.coalesce(func.sum(MouvementStock.quantite), 0)).filter(
                MouvementStock.type_mouvement.in_(['sortie', 'perte']),
                MouvementStock.date_mouvement >= day_start,
                MouvementStock.date_mouvement < day_end
            ).scalar()
            chart_data.append({
                'jour': day.strftime('%d/%m'),
                'entrees': int(entrees),
                'sorties': int(sorties)
            })

        # Top 5 produits les plus mouvementés
        top_produits = (db.session.query(
            Produit.nom,
            func.sum(MouvementStock.quantite).label('total_mv')
        ).join(MouvementStock).group_by(Produit.id)
         .order_by(desc('total_mv')).limit(5).all())

        stats = {
            'total_produits': total_produits,
            'total_categories': total_categories,
            'total_fournisseurs': total_fournisseurs,
            'valeur_totale': valeur_totale,
            'valeur_vente': valeur_vente,
            'nb_rupture': len(produits_rupture),
            'nb_stock_bas': len(produits_stock_bas),
        }

        return render_template('dashboard.html', stats=stats,
                               produits_rupture=produits_rupture,
                               produits_stock_bas=produits_stock_bas,
                               alertes_actives=alertes_actives,
                               mouvements_recents=mouvements_recents,
                               chart_data=chart_data,
                               top_produits=top_produits)

    # ==================================================================
    # ROUTES — Catégories
    # ==================================================================

    @app.route('/categories')
    @login_required
    def categories_list():
        categories = Categorie.query.order_by(Categorie.nom).all()
        return render_template('categories.html', categories=categories)

    @app.route('/categories/ajouter', methods=['GET', 'POST'])
    @manager_required
    def categorie_add():
        if request.method == 'POST':
            nom = request.form.get('nom', '').strip()
            description = request.form.get('description', '').strip()
            icone = request.form.get('icone', 'bi-cup-straw')
            couleur = request.form.get('couleur', 'primary')

            if Categorie.query.filter_by(nom=nom).first():
                flash('Cette catégorie existe déjà.', 'danger')
                return redirect(url_for('categorie_add'))

            cat = Categorie(nom=nom, description=description,
                            icone=icone, couleur=couleur)
            db.session.add(cat)
            db.session.commit()
            flash(f'Catégorie « {nom} » créée.', 'success')
            return redirect(url_for('categories_list'))
        return render_template('categorie_form.html', categorie=None)

    @app.route('/categories/<int:cat_id>/modifier', methods=['GET', 'POST'])
    @manager_required
    def categorie_edit(cat_id):
        cat = Categorie.query.get_or_404(cat_id)
        if request.method == 'POST':
            cat.nom = request.form.get('nom', cat.nom).strip()
            cat.description = request.form.get('description', '').strip()
            cat.icone = request.form.get('icone', cat.icone)
            cat.couleur = request.form.get('couleur', cat.couleur)
            db.session.commit()
            flash('Catégorie mise à jour.', 'success')
            return redirect(url_for('categories_list'))
        return render_template('categorie_form.html', categorie=cat)

    @app.route('/categories/<int:cat_id>/supprimer', methods=['POST'])
    @admin_required
    def categorie_delete(cat_id):
        cat = Categorie.query.get_or_404(cat_id)
        if cat.produits:
            flash('Impossible de supprimer : des produits sont liés à cette catégorie.', 'danger')
            return redirect(url_for('categories_list'))
        db.session.delete(cat)
        db.session.commit()
        flash('Catégorie supprimée.', 'info')
        return redirect(url_for('categories_list'))

    # ==================================================================
    # ROUTES — Fournisseurs
    # ==================================================================

    @app.route('/fournisseurs')
    @login_required
    def fournisseurs_list():
        fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
        return render_template('fournisseurs.html', fournisseurs=fournisseurs)

    @app.route('/fournisseurs/ajouter', methods=['GET', 'POST'])
    @manager_required
    def fournisseur_add():
        if request.method == 'POST':
            f = Fournisseur(
                nom=request.form.get('nom', '').strip(),
                contact=request.form.get('contact', '').strip(),
                telephone=request.form.get('telephone', '').strip(),
                email=request.form.get('email', '').strip(),
                adresse=request.form.get('adresse', '').strip(),
                notes=request.form.get('notes', '').strip()
            )
            db.session.add(f)
            db.session.commit()
            flash(f'Fournisseur « {f.nom} » ajouté.', 'success')
            return redirect(url_for('fournisseurs_list'))
        return render_template('fournisseur_form.html', fournisseur=None)

    @app.route('/fournisseurs/<int:fid>/modifier', methods=['GET', 'POST'])
    @manager_required
    def fournisseur_edit(fid):
        f = Fournisseur.query.get_or_404(fid)
        if request.method == 'POST':
            f.nom = request.form.get('nom', f.nom).strip()
            f.contact = request.form.get('contact', '').strip()
            f.telephone = request.form.get('telephone', '').strip()
            f.email = request.form.get('email', '').strip()
            f.adresse = request.form.get('adresse', '').strip()
            f.notes = request.form.get('notes', '').strip()
            db.session.commit()
            flash('Fournisseur mis à jour.', 'success')
            return redirect(url_for('fournisseurs_list'))
        return render_template('fournisseur_form.html', fournisseur=f)

    @app.route('/fournisseurs/<int:fid>/supprimer', methods=['POST'])
    @admin_required
    def fournisseur_delete(fid):
        f = Fournisseur.query.get_or_404(fid)
        if f.produits:
            flash('Impossible : des produits sont liés à ce fournisseur.', 'danger')
            return redirect(url_for('fournisseurs_list'))
        db.session.delete(f)
        db.session.commit()
        flash('Fournisseur supprimé.', 'info')
        return redirect(url_for('fournisseurs_list'))

    # ==================================================================
    # ROUTES — Produits
    # ==================================================================

    @app.route('/produits')
    @login_required
    def produits_list():
        cat_id = request.args.get('categorie', type=int)
        search = request.args.get('q', '').strip()
        filtre = request.args.get('filtre', '')

        query = Produit.query.filter_by(is_active=True)

        if cat_id:
            query = query.filter_by(categorie_id=cat_id)
        if search:
            query = query.filter(
                (Produit.nom.ilike(f'%{search}%')) |
                (Produit.code_barre.ilike(f'%{search}%')) |
                (Produit.marque.ilike(f'%{search}%'))
            )
        if filtre == 'rupture':
            query = query.filter(Produit.quantite_stock <= 0)
        elif filtre == 'stock_bas':
            query = query.filter(Produit.quantite_stock <= Produit.stock_minimum,
                                 Produit.quantite_stock > 0)
        elif filtre == 'surstock':
            query = query.filter(Produit.quantite_stock > Produit.stock_maximum)

        produits = query.order_by(Produit.nom).all()
        categories = Categorie.query.order_by(Categorie.nom).all()
        return render_template('produits.html', produits=produits,
                               categories=categories, cat_id=cat_id,
                               search=search, filtre=filtre)

    @app.route('/produits/ajouter', methods=['GET', 'POST'])
    @manager_required
    def produit_add():
        if request.method == 'POST':
            p = Produit(
                nom=request.form.get('nom', '').strip(),
                code_barre=request.form.get('code_barre', '').strip() or None,
                description=request.form.get('description', '').strip(),
                categorie_id=request.form.get('categorie_id', type=int),
                fournisseur_id=request.form.get('fournisseur_id', type=int) or None,
                quantite_stock=request.form.get('quantite_stock', 0, type=int),
                stock_minimum=request.form.get('stock_minimum', 10, type=int),
                stock_maximum=request.form.get('stock_maximum', 200, type=int),
                unite=request.form.get('unite', 'unité'),
                prix_achat=request.form.get('prix_achat', 0, type=float),
                prix_vente=request.form.get('prix_vente', 0, type=float),
                volume=request.form.get('volume', '').strip(),
                marque=request.form.get('marque', '').strip(),
                pays_origine=request.form.get('pays_origine', '').strip(),
                degre_alcool=request.form.get('degre_alcool', 0, type=float),
            )
            db.session.add(p)
            db.session.flush()

            # Enregistrer mouvement initial si stock > 0
            if p.quantite_stock > 0:
                enregistrer_mouvement(p, 'entree', p.quantite_stock,
                                      motif='Stock initial',
                                      prix_unit=p.prix_achat)
            else:
                verifier_alertes(p)

            db.session.commit()
            flash(f'Produit « {p.nom} » ajouté.', 'success')
            return redirect(url_for('produit_detail', pid=p.id))

        categories = Categorie.query.order_by(Categorie.nom).all()
        fournisseurs = Fournisseur.query.filter_by(is_active=True).order_by(Fournisseur.nom).all()
        return render_template('produit_form.html', produit=None,
                               categories=categories, fournisseurs=fournisseurs)

    @app.route('/produits/<int:pid>')
    @login_required
    def produit_detail(pid):
        produit = Produit.query.get_or_404(pid)
        mouvements = (MouvementStock.query.filter_by(produit_id=pid)
                      .order_by(MouvementStock.date_mouvement.desc())
                      .limit(50).all())
        return render_template('produit_detail.html', produit=produit,
                               mouvements=mouvements)

    @app.route('/produits/<int:pid>/modifier', methods=['GET', 'POST'])
    @manager_required
    def produit_edit(pid):
        p = Produit.query.get_or_404(pid)
        if request.method == 'POST':
            p.nom = request.form.get('nom', p.nom).strip()
            p.code_barre = request.form.get('code_barre', '').strip() or None
            p.description = request.form.get('description', '').strip()
            p.categorie_id = request.form.get('categorie_id', type=int)
            p.fournisseur_id = request.form.get('fournisseur_id', type=int) or None
            p.stock_minimum = request.form.get('stock_minimum', 10, type=int)
            p.stock_maximum = request.form.get('stock_maximum', 200, type=int)
            p.unite = request.form.get('unite', 'unité')
            p.prix_achat = request.form.get('prix_achat', 0, type=float)
            p.prix_vente = request.form.get('prix_vente', 0, type=float)
            p.volume = request.form.get('volume', '').strip()
            p.marque = request.form.get('marque', '').strip()
            p.pays_origine = request.form.get('pays_origine', '').strip()
            p.degre_alcool = request.form.get('degre_alcool', 0, type=float)
            db.session.commit()
            flash('Produit mis à jour.', 'success')
            return redirect(url_for('produit_detail', pid=p.id))

        categories = Categorie.query.order_by(Categorie.nom).all()
        fournisseurs = Fournisseur.query.filter_by(is_active=True).order_by(Fournisseur.nom).all()
        return render_template('produit_form.html', produit=p,
                               categories=categories, fournisseurs=fournisseurs)

    @app.route('/produits/<int:pid>/supprimer', methods=['POST'])
    @admin_required
    def produit_delete(pid):
        p = Produit.query.get_or_404(pid)
        p.is_active = False  # Soft delete
        db.session.commit()
        flash(f'Produit « {p.nom} » désactivé.', 'info')
        return redirect(url_for('produits_list'))

    # ==================================================================
    # ROUTES — Mouvements de stock
    # ==================================================================

    @app.route('/mouvements')
    @login_required
    def mouvements_list():
        type_mv = request.args.get('type', '')
        produit_id = request.args.get('produit_id', type=int)

        query = MouvementStock.query

        if type_mv:
            query = query.filter_by(type_mouvement=type_mv)
        if produit_id:
            query = query.filter_by(produit_id=produit_id)

        mouvements = (query.order_by(MouvementStock.date_mouvement.desc())
                      .limit(200).all())
        produits = Produit.query.filter_by(is_active=True).order_by(Produit.nom).all()
        return render_template('mouvements.html', mouvements=mouvements,
                               produits=produits, type_mv=type_mv,
                               produit_id=produit_id)

    @app.route('/mouvements/entree', methods=['GET', 'POST'])
    @login_required
    def mouvement_entree():
        if request.method == 'POST':
            produit_id = request.form.get('produit_id', type=int)
            quantite = request.form.get('quantite', 0, type=int)
            motif = request.form.get('motif', '').strip()
            reference = request.form.get('reference', '').strip()
            prix_unit = request.form.get('prix_unitaire', 0, type=float)
            notes = request.form.get('notes', '').strip()

            if quantite <= 0:
                flash('La quantité doit être supérieure à 0.', 'danger')
                return redirect(url_for('mouvement_entree'))

            produit = Produit.query.get_or_404(produit_id)
            enregistrer_mouvement(produit, 'entree', quantite, motif,
                                  reference, prix_unit, notes)
            # Mise à jour prix achat si fourni
            if prix_unit > 0:
                produit.prix_achat = prix_unit
            db.session.commit()
            flash(f'Entrée de {quantite} {produit.unite}(s) pour « {produit.nom} ».', 'success')
            return redirect(url_for('produit_detail', pid=produit.id))

        produits = Produit.query.filter_by(is_active=True).order_by(Produit.nom).all()
        return render_template('mouvement_form.html', type_mv='entree',
                               produits=produits)

    @app.route('/mouvements/sortie', methods=['GET', 'POST'])
    @login_required
    def mouvement_sortie():
        if request.method == 'POST':
            produit_id = request.form.get('produit_id', type=int)
            quantite = request.form.get('quantite', 0, type=int)
            motif = request.form.get('motif', '').strip()
            reference = request.form.get('reference', '').strip()
            notes = request.form.get('notes', '').strip()

            if quantite <= 0:
                flash('La quantité doit être supérieure à 0.', 'danger')
                return redirect(url_for('mouvement_sortie'))

            produit = Produit.query.get_or_404(produit_id)
            if quantite > produit.quantite_stock:
                flash(f'Stock insuffisant ({produit.quantite_stock} disponible).', 'danger')
                return redirect(url_for('mouvement_sortie'))

            enregistrer_mouvement(produit, 'sortie', quantite, motif,
                                  reference, produit.prix_vente, notes)
            db.session.commit()
            flash(f'Sortie de {quantite} {produit.unite}(s) pour « {produit.nom} ».', 'success')
            return redirect(url_for('produit_detail', pid=produit.id))

        produits = Produit.query.filter_by(is_active=True).order_by(Produit.nom).all()
        return render_template('mouvement_form.html', type_mv='sortie',
                               produits=produits)

    @app.route('/mouvements/ajustement', methods=['GET', 'POST'])
    @manager_required
    def mouvement_ajustement():
        if request.method == 'POST':
            produit_id = request.form.get('produit_id', type=int)
            quantite = request.form.get('quantite', 0, type=int)
            motif = request.form.get('motif', '').strip() or 'Ajustement inventaire'
            notes = request.form.get('notes', '').strip()

            produit = Produit.query.get_or_404(produit_id)
            enregistrer_mouvement(produit, 'ajustement', quantite, motif,
                                  notes=notes)
            db.session.commit()
            flash(f'Stock de « {produit.nom} » ajusté à {quantite}.', 'success')
            return redirect(url_for('produit_detail', pid=produit.id))

        produits = Produit.query.filter_by(is_active=True).order_by(Produit.nom).all()
        return render_template('mouvement_form.html', type_mv='ajustement',
                               produits=produits)

    # ==================================================================
    # ROUTES — Alertes
    # ==================================================================

    @app.route('/alertes')
    @login_required
    def alertes_list():
        alertes = (Alerte.query.filter_by(is_resolved=False)
                   .order_by(Alerte.created_at.desc()).all())
        return render_template('alertes.html', alertes=alertes)

    @app.route('/alertes/<int:aid>/lire', methods=['POST'])
    @login_required
    def alerte_read(aid):
        alerte = Alerte.query.get_or_404(aid)
        alerte.is_read = True
        db.session.commit()
        return redirect(url_for('alertes_list'))

    @app.route('/alertes/tout-lire', methods=['POST'])
    @login_required
    def alertes_read_all():
        Alerte.query.filter_by(is_read=False).update({'is_read': True})
        db.session.commit()
        flash('Toutes les alertes marquées comme lues.', 'info')
        return redirect(url_for('alertes_list'))

    # ==================================================================
    # ROUTES — Rapports
    # ==================================================================

    @app.route('/rapports')
    @manager_required
    def rapports():
        return render_template('rapports.html')

    @app.route('/rapports/inventaire')
    @manager_required
    def rapport_inventaire():
        produits = (Produit.query.filter_by(is_active=True)
                    .order_by(Produit.categorie_id, Produit.nom).all())
        categories = Categorie.query.order_by(Categorie.nom).all()
        valeur_totale = sum(p.valeur_stock for p in produits)
        return render_template('rapport_inventaire.html', produits=produits,
                               categories=categories, valeur_totale=valeur_totale)

    @app.route('/rapports/mouvements')
    @manager_required
    def rapport_mouvements():
        date_debut = request.args.get('date_debut', '')
        date_fin = request.args.get('date_fin', '')

        query = MouvementStock.query
        if date_debut:
            query = query.filter(MouvementStock.date_mouvement >= datetime.strptime(date_debut, '%Y-%m-%d'))
        if date_fin:
            date_fin_dt = datetime.strptime(date_fin, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(MouvementStock.date_mouvement < date_fin_dt)

        mouvements = query.order_by(MouvementStock.date_mouvement.desc()).all()

        # Résumé par type
        resume = {}
        for mv in mouvements:
            if mv.type_mouvement not in resume:
                resume[mv.type_mouvement] = {'count': 0, 'quantite': 0, 'montant': 0}
            resume[mv.type_mouvement]['count'] += 1
            resume[mv.type_mouvement]['quantite'] += mv.quantite
            resume[mv.type_mouvement]['montant'] += mv.montant_total

        return render_template('rapport_mouvements.html', mouvements=mouvements,
                               resume=resume, date_debut=date_debut, date_fin=date_fin)

    @app.route('/rapports/export/inventaire')
    @manager_required
    def export_inventaire():
        produits = Produit.query.filter_by(is_active=True).order_by(Produit.nom).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Nom', 'Code-barre', 'Catégorie', 'Marque', 'Volume',
                          'Stock', 'Min', 'Max', 'Unité', 'Prix Achat', 'Prix Vente',
                          'Valeur Stock', 'Fournisseur'])
        for p in produits:
            writer.writerow([
                p.nom, p.code_barre or '', p.categorie.nom if p.categorie else '',
                p.marque or '', p.volume or '', p.quantite_stock, p.stock_minimum,
                p.stock_maximum, p.unite, p.prix_achat, p.prix_vente,
                round(p.valeur_stock, 2),
                p.fournisseur.nom if p.fournisseur else ''
            ])
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment;filename=inventaire_mille_oceans.csv'}
        )

    # ==================================================================
    # ROUTES — Administration
    # ==================================================================

    @app.route('/admin/utilisateurs')
    @admin_required
    def admin_users():
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin_users.html', users=users)

    @app.route('/admin/utilisateurs/<int:uid>/role', methods=['POST'])
    @admin_required
    def admin_change_role(uid):
        user = User.query.get_or_404(uid)
        if user.id == current_user.id:
            flash('Vous ne pouvez pas modifier votre propre rôle.', 'danger')
            return redirect(url_for('admin_users'))
        new_role = request.form.get('role', 'staff')
        if new_role in ('admin', 'manager', 'staff'):
            user.role = new_role
            db.session.commit()
            flash(f"Rôle de {user.full_name} changé en « {new_role} ».", 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/utilisateurs/<int:uid>/toggle', methods=['POST'])
    @admin_required
    def admin_toggle_user(uid):
        user = User.query.get_or_404(uid)
        if user.id == current_user.id:
            flash('Vous ne pouvez pas désactiver votre propre compte.', 'danger')
            return redirect(url_for('admin_users'))
        user.is_active = not user.is_active
        db.session.commit()
        status = 'activé' if user.is_active else 'désactivé'
        flash(f"Compte de {user.full_name} {status}.", 'info')
        return redirect(url_for('admin_users'))

    # ==================================================================
    # CLI
    # ==================================================================

    @app.cli.command('init-db')
    def init_db():
        db.create_all()
        print('Base de données initialisée.')

    @app.cli.command('seed-db')
    def seed_db():
        """Données de démonstration."""
        db.create_all()
        if Categorie.query.first():
            print('Données déjà présentes.')
            return

        # Catégories
        cats_data = [
            ('Bières', 'Bières locales et importées', 'bi-cup-straw', 'warning'),
            ('Vins', 'Vins rouges, blancs, rosés', 'bi-cup', 'danger'),
            ('Spiritueux', 'Whisky, Rhum, Vodka, Gin…', 'bi-droplet', 'info'),
            ('Jus & Sodas', 'Boissons non alcoolisées', 'bi-cup-hot', 'success'),
            ('Eaux', 'Eaux plates et gazeuses', 'bi-water', 'primary'),
            ('Champagne & Mousseux', 'Champagnes et vins effervescents', 'bi-stars', 'secondary'),
            ('Boissons Spéciaux', 'Cocktails, boissons préparées et spécialités maison', 'bi-tropical-storm', 'dark'),
        ]
        cats = {}
        for nom, desc, icone, couleur in cats_data:
            c = Categorie(nom=nom, description=desc, icone=icone, couleur=couleur)
            db.session.add(c)
            db.session.flush()
            cats[nom] = c

        # Fournisseurs
        fourns_data = [
            ('Brasserie Nationale', 'Jean Dupont', '+509 3456-7890', 'contact@brasserie.ht'),
            ('Import Caraïbes', 'Marie Claire', '+509 4567-8901', 'info@importcaraibes.com'),
            ('Vins & Plus', 'Pierre Louis', '+509 5678-9012', 'pierre@vinsetplus.com'),
            ('Boissons Express', 'Sophie Martin', '+509 6789-0123', 'sophie@boissonsexpress.com'),
        ]
        fourns = {}
        for nom, contact, tel, email in fourns_data:
            f = Fournisseur(nom=nom, contact=contact, telephone=tel, email=email)
            db.session.add(f)
            db.session.flush()
            fourns[nom] = f

        # Produits
        produits_data = [
            ('Prestige (grande)', cats['Bières'], fourns['Brasserie Nationale'], 65, 'unité', 50, 300, 75, 120, '620ml', 'Prestige', 'Haïti', 5.2),
            ('Prestige (petite)', cats['Bières'], fourns['Brasserie Nationale'], 120, 'unité', 80, 500, 50, 75, '330ml', 'Prestige', 'Haïti', 5.2),
            ('Heineken', cats['Bières'], fourns['Import Caraïbes'], 45, 'unité', 30, 200, 100, 150, '330ml', 'Heineken', 'Pays-Bas', 5.0),
            ('Corona Extra', cats['Bières'], fourns['Import Caraïbes'], 30, 'unité', 20, 150, 110, 160, '355ml', 'Corona', 'Mexique', 4.5),
            ('Guinness', cats['Bières'], fourns['Import Caraïbes'], 8, 'unité', 15, 100, 120, 175, '440ml', 'Guinness', 'Irlande', 4.2),
            ('Brakina', cats['Bières'], fourns['Brasserie Nationale'], 60, 'unité', 30, 250, 45, 85, '65cl', 'Brakina', 'Burkina Faso', 4.2),
            ('Flag', cats['Bières'], fourns['Brasserie Nationale'], 55, 'unité', 30, 250, 40, 80, '65cl', 'Flag', 'Maroc', 5.0),
            ('Beaufort', cats['Bières'], fourns['Brasserie Nationale'], 70, 'unité', 30, 250, 40, 80, '65cl', 'Beaufort', 'Cameroun', 5.2),
            ('Vin Rouge Merlot', cats['Vins'], fourns['Vins & Plus'], 25, 'bouteille', 10, 80, 450, 800, '75cl', 'Mouton Cadet', 'France', 13.5),
            ('Vin Blanc Chardonnay', cats['Vins'], fourns['Vins & Plus'], 18, 'bouteille', 10, 60, 400, 750, '75cl', 'Chablis', 'France', 12.5),
            ('Rosé Provence', cats['Vins'], fourns['Vins & Plus'], 3, 'bouteille', 8, 50, 500, 850, '75cl', 'Minuty', 'France', 12.0),
            ('Rhum Barbancourt 5 étoiles', cats['Spiritueux'], fourns['Brasserie Nationale'], 40, 'bouteille', 15, 100, 600, 1000, '75cl', 'Barbancourt', 'Haïti', 43.0),
            ('Rhum Barbancourt 3 étoiles', cats['Spiritueux'], fourns['Brasserie Nationale'], 55, 'bouteille', 20, 120, 350, 600, '75cl', 'Barbancourt', 'Haïti', 40.0),
            ('Hennessy VS', cats['Spiritueux'], fourns['Import Caraïbes'], 12, 'bouteille', 5, 40, 1500, 2500, '70cl', 'Hennessy', 'France', 40.0),
            ('Vodka Absolut', cats['Spiritueux'], fourns['Import Caraïbes'], 20, 'bouteille', 10, 60, 700, 1200, '70cl', 'Absolut', 'Suède', 40.0),
            ('Coca-Cola', cats['Jus & Sodas'], fourns['Boissons Express'], 200, 'unité', 100, 600, 25, 50, '355ml', 'Coca-Cola', 'USA', 0.0),
            ('Sprite', cats['Jus & Sodas'], fourns['Boissons Express'], 150, 'unité', 80, 500, 25, 50, '355ml', 'Sprite', 'USA', 0.0),
            ('Jus de Mangue Tampico', cats['Jus & Sodas'], fourns['Boissons Express'], 90, 'unité', 50, 300, 30, 60, '500ml', 'Tampico', 'USA', 0.0),
            ('Red Bull', cats['Jus & Sodas'], fourns['Import Caraïbes'], 60, 'unité', 30, 200, 100, 175, '250ml', 'Red Bull', 'Autriche', 0.0),
            ('Fanta Orange', cats['Jus & Sodas'], fourns['Boissons Express'], 180, 'unité', 80, 500, 25, 50, '33cl', 'Fanta', 'Côte d\'Ivoire', 0.0),
            ('Eau Culligan (grande)', cats['Eaux'], fourns['Boissons Express'], 0, 'unité', 50, 400, 15, 30, '1.5L', 'Culligan', 'Haïti', 0.0),
            ('Eau Culligan (petite)', cats['Eaux'], fourns['Boissons Express'], 180, 'unité', 100, 800, 8, 20, '500ml', 'Culligan', 'Haïti', 0.0),
            ('Champagne Moët & Chandon', cats['Champagne & Mousseux'], fourns['Vins & Plus'], 6, 'bouteille', 3, 20, 3000, 5500, '75cl', 'Moët', 'France', 12.0),
            ('Prosecco Mionetto', cats['Champagne & Mousseux'], fourns['Vins & Plus'], 15, 'bouteille', 5, 30, 800, 1400, '75cl', 'Mionetto', 'Italie', 11.0),
            ('Mojito Prêt-à-Boire', cats['Boissons Spéciaux'], fourns['Import Caraïbes'], 40, 'bouteille', 20, 150, 200, 400, '33cl', 'Caribbean Mix', 'Caraïbes', 5.0),
            ('Piña Colada Prête', cats['Boissons Spéciaux'], fourns['Import Caraïbes'], 35, 'bouteille', 15, 120, 220, 450, '33cl', 'Caribbean Mix', 'Caraïbes', 5.5),
            ('Punch Passion Maison', cats['Boissons Spéciaux'], fourns['Brasserie Nationale'], 25, 'bouteille', 10, 80, 180, 350, '50cl', 'Mille Océans', 'Haïti', 8.0),
            ('Sangria Rouge', cats['Boissons Spéciaux'], fourns['Vins & Plus'], 20, 'bouteille', 10, 60, 300, 550, '75cl', 'Soleil', 'Espagne', 7.0),
            ('Smoothie Tropical', cats['Boissons Spéciaux'], fourns['Boissons Express'], 50, 'unité', 25, 200, 80, 175, '50cl', 'Mille Océans', 'Haïti', 0.0),
        ]

        for (nom, cat, fourn, qty, unite, smin, smax, pachat, pvente,
             vol, marque, pays, alcool) in produits_data:
            p = Produit(
                nom=nom, categorie_id=cat.id, fournisseur_id=fourn.id,
                quantite_stock=qty, unite=unite, stock_minimum=smin,
                stock_maximum=smax, prix_achat=pachat, prix_vente=pvente,
                volume=vol, marque=marque, pays_origine=pays,
                degre_alcool=alcool
            )
            db.session.add(p)

        db.session.commit()
        print('Données de démonstration insérées (catégories, fournisseurs, 20 produits).')

    # ------------------------------------------------------------------
    # Init DB on first run
    # ------------------------------------------------------------------
    with app.app_context():
        db.create_all()

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
