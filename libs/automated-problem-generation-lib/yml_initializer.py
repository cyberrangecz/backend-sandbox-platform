import json
import yaml

##all functions : def __repr__(self): are only for test purpose

#basic class to store data about user used in Task class
class User:

    def __init__(self, username, password):
        self.name = username
        self.password = str("{{ \'%s\' | password_hash('sha512') }}" % password)

    def __repr__(self):
        return str(self.__dict__)

#task object used in Action class
class Task:

    def __init__(self, new_user = User("admin", "password"), task_name="add new user"):
        self.name = task_name
        self.user = new_user

    def __repr__(self):
        return str(self.__dict__)


#top level structure to save all data necessary for .yml file
class Action:
    def __init__(self, tasks=[Task(User("admin", "password"))], name="create new users with selected passwords",
                 host="all", become=True, become_user="root", ):
        self.name = name
        self.hosts = host
        self.become = become
        self.become_user = become_user
        self.tasks = tasks

    def __repr__(self):
        return str(self.__dict__)


#return dictionary structure of object
def call_dict_rec(object):
    try:
        res = {}
        keys = object.__dict__.keys()
        for atr in keys:
            #list
            if isinstance((object.__dict__[atr]), list):
                actual_list = []
                for element in (object.__dict__[atr]):
                    actual_list.append(call_dict_rec(element))
                res[atr] = actual_list
            #object
            else:
                actual_atr = call_dict_rec(object.__dict__[atr])
                res[atr] = actual_atr
        return res
    except: #it means it is last level value
        return object


#create .yml file from list of Action object and save it at path
def create_playbook(actions, path='./provisioning/playbook.yml'):
    for action in actions:
        with open(path, 'w') as file:
            documents = yaml.dump([call_dict_rec(action)], file)


#read "username" and "password" from file located at "path" and return User object
def read_tasks_to_create(path='name.yml'):
    with open(path) as json_file:
        data = json.load(json_file)
        user_to_add = User(data['username'], data['password'])
        return user_to_add

#create playbook.yml from data stored in name.yml
def add_user():
    #all plays/actions to be add into .yml file
    actions_to_add = []
    # all task to be add into current play/action
    tasks_to_add = []

    #add actual task <add user> imported from file
    tasks_to_add.append(Task(read_tasks_to_create(),"test task to add user"))
    #add all current task into play/action
    actions_to_add.append(Action(tasks_to_add,"test create new users with selected passwords"))
    #finaly create playbook
    create_playbook(actions_to_add)
