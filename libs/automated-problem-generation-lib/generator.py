from random import randint
from distutils.dir_util import copy_tree
import jinja2
import crypt, json, os, sys

name_file = "names.txt"
password_file = "passwords.txt"


def get_random_name(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        return l[randint(0, len(l) - 1)]


def get_random_port():
    return str(35400 + randint(0, 4600))


def get_random_password(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        visible = l[randint(0, len(l) - 1)]
        return [visible, crypt.crypt(visible, crypt.mksalt(crypt.METHOD_SHA512))]


def generate_playbook(dic, path):
    if not os.path.exists(path):
        os.makedirs(path)

    copy_tree("./Base_config", path)

    templateLoader = jinja2.FileSystemLoader(searchpath=path + "/templates")
    templateEnv = jinja2.Environment(loader=templateLoader)
    TEMPLATE_FILE = "user_telnet_port"
    template = templateEnv.get_template(TEMPLATE_FILE)
    outputText = template.render(name=dic["username"],
                                 password="'{{ ''" + dic["password"] + "'' | password_hash(''sha512'') }}'",
                                 port=dic["port"])

    f = open(path + "/provisioning/playbook.yml", "w")
    f.write(outputText)
    f.close


def generate_randomized_games(num):
    for i in range(num):
        # print(get_random_password("names.txt"))
        data = {}
        data['port'] = get_random_port() + '/tcp'
        data['username'] = get_random_name(name_file)
        data['my_password'] = get_random_password(password_file)[1]
        data['password'] = get_random_password(password_file)[0]
        mode = 'a'
        if (i == 0):
            mode = 'w'
        with open('players.yml', mode) as outfile:
            outfile.write("player_id_" + str(i) + ":  ")
            json.dump(data, outfile)
            outfile.write('\n')
        generate_playbook(data, "player_id_" + str(i))


generate_randomized_games(int(sys.argv[1]))
