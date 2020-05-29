from random import randint
import crypt, json
import yml_initializer


name_file = "names.txt"
password_file = "passwords.txt"


def get_random_name(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        return l[randint(0, len(l) - 1)]


def get_random_password(fname):
    l = []
    with open(fname, "r") as f:
        for name in f:
            l.append(name[:-1])
        visible = l[randint(0, len(l) - 1)]
        return [visible, crypt.crypt(visible, crypt.mksalt(crypt.METHOD_SHA512))]


# print(get_random_password("names.txt"))
data = {}
data['username'] = get_random_name(name_file)
data['my_password'] = get_random_password(password_file)[1]
data['password'] = get_random_password(password_file)[0]

with open('name.yml', 'w') as outfile:
    json.dump(data, outfile)

#yml_initializer.add_user()
