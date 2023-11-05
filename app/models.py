import flask
import hashlib
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from flask_login import AnonymousUserMixin, UserMixin, login_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

from . import db, login_manager
from .utilities.geolocator import generate_coordinates
from .utilities.file_saver import save_image


class PaginatedAPIMixin(object):
    @staticmethod
    def to_collection_dict(query, page, per_page, endpoint, **kwargs):
        resources = query.paginate(page=page, per_page=per_page,
                error_out=False)
        data = {
                'items': [item.to_dict() for item in resources.items]
                }
        return data


class Permission:
    VISIT = 1
    MEMBER = 2
    MODERATE = 4
    ADMIN = 8


@login_manager.user_loader
def load_user(user_id):
    """
    Queries the database for a record of currently logged in user
    Returns User object containing info about logged in user
    """
    return User.query.get(int(user_id))


class Role(db.Model):
    __tablename__ = 'role'
    roleId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False)
    default = db.Column(db.Boolean, default = False, index = True)
    permissions = db.Column(db.Integer)

    users = db.relationship('User', backref = 'role', lazy = 'dynamic')


    def __init__(self, **kwargs):
        super(Role, self).__init__(**kwargs)
        if self.permissions is None:
            self.permissions = 0


    @staticmethod
    def insert_roles():
        roles = {
                'Guest' : [Permission.VISIT],
                'Member' : [Permission.VISIT, Permission.MEMBER],
                'Administrator' : [Permission.VISIT, Permission.MODERATE,
                    Permission.MEMBER, Permission.ADMIN]
                }

        default_role = 'Guest'

        for r in roles:
            role = Role.query.filter_by(name = r).first()
            if role in None:
                role = Role(name = r)

            role.reset_permissions()
            for perm in roles[r]:
                role.add_permission(perm)

            role.default = (role.name == default_role)
            db.session.add(role)
        db.session.commit()


    def add_permission(self, perm):
        if not self.has_permission(perm):
            self.permissions += perm


    def remove_permission(self, perm):
        if self.has_permission(perm):
            self.permissions -= perm


    def reset_permissions(self):
        self.permissions = 0


    def has_permission(self, perm):
        return self.permissions & perm == perm


    def __repr__(self):
        return f"<Role(roleId={self.roleId}, name='{self.name}')>"


class Anonymous_User(AnonymousUserMixin):
    def can(self, permission):
        return False


    def is_administrator(self):
        return False


login_manager.anonymous_user = Anonymous_User


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    userId = db.Column(db.Integer, primary_key=True, autoincrement=True, index=True)
    firstName = db.Column(db.String(40))
    middleName = db.Column(db.String(40))
    lastName = db.Column(db.String(40))
    username = db.Column(db.String(255), nullable=False, unique=True, index=True)
    emailAddress = db.Column(db.String(255), nullable=False, unique=True, index=True)
    phoneNumber = db.Column(db.String(255), nullable=False, unique=True, index=True)
    passwordHash = db.Column(db.String(255), nullable=False)
    imageUrl = db.Column(db.String(255))

    # relationships
    roleId = db.Column(db.Integer, db.ForeignKey('role.roleId'))

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)

        # Assign default role to user
        if self.role is None:
            if self.emailAddress == flask.current_app.config['ADMINISTRATOR_EMAIL']:
                self.role = Role.query.filter_by(name = 'Administrator').first()

            if self.role is None:
                self.role = Role.query.filter_by(default = True).first()

        # Generate avatar hash
        if self.emailAddress is not None and self.avatar_hash is None:
            self.avatar_hash = self.gravatar_hash()


    def get_id(self):
        return self.userId


    def gravatar_hash(self):
        return hashlib.md5(self.emailAddress.lower().encode('utf-8')).hexdigest()


    def gravatar(self, size = 100, default = 'identicon', rating = 'g'):
        url = 'https://secure.gravatar.com/avatar'
        hash = self.avatar_hash or self.gravatar_hash()
        return "{url}/{hash}?s={size}&d={default}&r={rating}".format(url = url,
                hash = hash, size = size, default = default, rating = rating)

    @property
    def password(self):
        raise AttributeError("Password is not a readable attribute")


    @password.setter
    def password(self, password):
        self.passwordHash = generate_password_hash(password)


    def verify_password(self, password):
        return check_password_hash(self.passwordHash, password)


    @staticmethod
    def reset_password(token, new_password):
        serializer = Serializer(flask.current_app.config['SECRET_KEY'])
        try:
            data = serializer.loads(token.encode('utf-8'))
        except:
            return False

        user = User.query.get(data.get('reset'))
        if user is None:
            return False

        user.password = new_password
        db.session.add(user)
        return True


    def can(self, perm):
        return self.role is not None and self.role.has_permission(perm)


    def is_administrator(self):
        return self.can(Permission.ADMIN)


    def register(self, form):
        self.username = form.get('username')
        self.password = form.get('password')
        self.emailAddress = form.get('email')
        self.phoneNumber = form.get('phone_number')

        db.session.add(self)
        db.session.commit()

    
    def login(self, password):
        if self.verify_password(password):
            login_user(self)
            return True

        else:
            return False


    def scheduleEvent(self, form = None, image_file = None):
        start = datetime.strptime(form.get("startDateTime"), "%Y-%m-%dT%H:%M")
        end = datetime.strptime(form.get("endDateTime"), "%Y-%m-%dT%H:%M")
        
        # Save image on file system
        image_url = save_image(image_file, 
                flask.current_app.config["EVENT_IMAGES_UPLOAD_PATH"])
        
        event = Event(
                title = form.get("title"),
                description = form.get("description"),
                startDateTime = start,
                endDateTime = end,
                venue = form.get("venue"),
                organizer = form.get("organizer"),
                imageUrl = image_file.filename
                )
        
        event.creatorId = current_user.userId
        # Get latitude and longitude
        latitude, longitude = generate_coordinates(form.get("venue"))
        if latitude and longitude:
            event.latitude = latitude
            event.longitude = longitude

        db.session.add(event)
        db.session.commit()
        return True

    
    def getCreatedEvents(self):
        page = flask.request.args.get('page', 1, type = int)
        per_page = min(flask.request.args.get('per_page', 10, type = int), 100)
        records = Event.query.filter(Event.creatorId == current_user.userId)
        return records


    def addRecord(self, form = None, image_file = None):
        # Save image on file system
        image_url = save_image(image_file, 
                flask.current_app.config["EVENT_IMAGES_UPLOAD_PATH"])
        
        record = Record(
                species = form.get("species"),
                datePlanted = datetime.strptime(form.get("datePlanted"),
                    "%Y-%m-%d"),
                numberOfTrees = form.get("numberOfTrees"),
                location = form.get("location"),
                imageUrl = image_file.filename
                )
        record.userId = current_user.userId

        # Get latitude and longitude
        latitude, longitude = generate_coordinates(form.get("location"))
        if latitude and longitude:
            record.latitude = latitude
            record.longitude = longitude

        db.session.add(record)
        db.session.commit()
        return True


    def getRecords(self):
        page = flask.request.args.get('page', 1, type = int)
        per_page = min(flask.request.args.get('per_page', 10, type = int), 100)
        records = Record.query.filter(Record.userId == current_user.userId)
        return records


class Event(db.Model):
    __tablename__ = 'event'

    eventId = db.Column(db.Integer, primary_key=True, autoincrement=True, index=True)
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    imageUrl = db.Column(db.String(255))
    startDateTime = db.Column(db.DateTime)
    endDateTime = db.Column(db.DateTime)
    venue = db.Column(db.String(255))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    organizer = db.Column(db.String(255))
    creatorId = db.Column(db.Integer, nullable = False)
    isCancelled = db.Column(db.Boolean, default=False)
    dateCreated = db.Column(db.DateTime, default=datetime.utcnow)
    lastUpdated = db.Column(db.DateTime, default = datetime.utcnow, 
            onupdate=datetime.utcnow)

    def getEventDetails(self):
        return self.to_dict()

    def to_dict(self):
        data = {
                'eventId': self.eventId,
                'title': self.title,
                'description': self.description,
                'startDateTime': self.startDateTime,
                'endDateTime': self.endDateTime,
                'venue': self.venue,
                'organizer': self.organizer,
                'imageUrl': self.imageUrl,
                'isCancelled': self.isCancelled,
                'latitude': self.latitude,
                'longitude': self.longitude,
                'dateCreated': self.dateCreated.isoformat() + 'Z',
                'lastUpdated': self.dateCreated.isoformat() + 'Z'
                }
        return data


class Record(PaginatedAPIMixin, db.Model):
    __tablename__ = 'record'

    recordId = db.Column(db.Integer, primary_key=True, autoincrement=True, index=True)
    userId = db.Column(db.Integer, db.ForeignKey('user.userId'))
    species = db.Column(db.String(255))
    dateCreated = db.Column(db.DateTime, default=datetime.utcnow)
    datePlanted = db.Column(db.Date)
    numberOfTrees = db.Column(db.Integer)
    imageUrl = db.Column(db.String(255))
    location = db.Column(db.String(255))
    longitude = db.Column(db.Float)
    latitude = db.Column(db.Float)
    lastUpdated = db.Column(db.DateTime, default = datetime.utcnow, 
            onupdate=datetime.utcnow)
    isConfirmed = db.Column(db.Boolean, default=False)
    isRevoked = db.Column(db.Boolean, default=False)

    def to_dict(self):
        data = {
                'recordId': self.recordId,
                'species': self.species,
                'dateCreated': self.dateCreated.isoformat() + 'Z',
                'datePlanted': self.datePlanted.isoformat() + 'Z',
                'numberOfTrees': self.numberOfTrees,
                'imageUrl': self.imageUrl,
                'location': self.location,
                'isConfirmed': self.isConfirmed,
                'isRevoked': self.isRevoked,
                'lastUpdated': self.lastUpdated,
                'latitude': self.latitude,
                'longitude': self.longitude
                }
        return data


class RegisteredEvent(db.Model):
    __tablename__ = 'registered_event'

    registeredEventId = db.Column(db.Integer, primary_key=True, autoincrement=True, index=True)
    userId = db.Column(db.Integer, db.ForeignKey('user.userId'))
    eventId = db.Column(db.Integer, db.ForeignKey('event.eventId'))
    isCancelled = db.Column(db.Boolean, default=False)
    isAttended = db.Column(db.Boolean, default=False)
    dateCreated = db.Column(db.DateTime, default=datetime.utcnow)
    lastUpdated = db.Column(db.DateTime, default = datetime.utcnow, 
            onupdate=datetime.utcnow)
