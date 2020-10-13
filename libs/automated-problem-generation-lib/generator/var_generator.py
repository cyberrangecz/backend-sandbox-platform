import string, random, os

name_file_path = "names.txt"

'''
Function generate random name.

Parameters
    ----------
    file : corresponding file object 
        path to file with names

Returns
    ------- 
    string 
        one of the names from text file

'''
def get_random_name(name_file):
    l = []
    with open(name_file, "r") as f:
        for name in f:
            l.append(name[:-1])
        return l[random.randint(0, len(l) - 1)]


'''
Function generate random port number with optional restrictions.

Parameters
    ----------
    var_obj : Variable object
        Variable object with setted restrictions for generation

Returns
    ------- 
    Variable object
        Variable object with filled generated_value attribute

'''
def get_random_port(var_obj):
    min = var_obj.min
    max = var_obj.max
    if min == None:
        min = 35000
    if max == None:
        max = min + 4000

    while True:
        port = random.randint(min, max)
        if not port in var_obj.prohibited:
            return str(port)

'''
Function generate random IP address with optional restrictions.

Parameters
    ----------
    var_obj : Variable object
        Variable object with setted restrictions for generation

Returns
    ------- 
    Variable object
        Variable object with filled generated_value attribute

'''

def get_random_IP(var_obj):
    octet_list_min = (var_obj.min or " ").split(".")
    octet_list_max = (var_obj.max or " ").split(".")

    if len(octet_list_min) <= 3:
        octet_list_min = [0, 0, 0, 0]
    if len(octet_list_max) <= 3:
        octet_list_min = [255, 255, 255, 255]

    for i in range(4):
        if int(octet_list_min[i]) > 255:
            octet_list_min[i] = 255
        else:
            octet_list_min[i] = int(octet_list_min[i])
        if int(octet_list_max[i]) > 255:
            octet_list_max[i] = 255
        else:
            octet_list_max[i] = int(octet_list_max[i])

    while True:
        ip_dec = random.randint(octet_list_min[0] * 2 ** 24 + octet_list_min[1] * 2 ** 16 + octet_list_min[2] * 2 ** 8 +
                                octet_list_min[3],
                                octet_list_max[0] * 2 ** 24 + octet_list_max[1] * 2 ** 16 + octet_list_max[2] * 2 ** 8 +
                                octet_list_max[3])
        ip = ""
        for i in range(4):
            ip = str(ip_dec % 2 ** 8) + "." + ip
            ip_dec //= 2 ** 8

        if not ip in var_obj.prohibited:
            return ip[:-1]

'''
Helper function to get absolut path to the file.

Parameters
    ----------
    file : string
        relative path to file
Returns
    ------- 
    string
        absolut path to the file

'''
def get_cwd(file):
    _ROOT = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(_ROOT, file)

'''
Function generate random password.

Parameters
    ----------
    lenth : int
        number of characters in result 

Returns
    ------- 
    string 
        generated password

'''
def get_random_password(length):
    letters_and_digits = string.ascii_letters + string.digits
    result_str = ''.join((random.choice(letters_and_digits) for i in range(length)))
    return result_str


'''
Functions fill each Variable object's attribute generated_value from argument with generated value.


Parameters
    ----------
    variables : list
        list of Variable objects
        
Returns
    ------- 
    list of Variable objects
        list of Variable objects filled with generate valued in dependece on restrictions
'''
def generate_randomized_arg(variables):
    for var in variables:
        if (var.type).lower() == 'username':
            path = name_file_path
            var.generated_value = get_random_name(get_cwd(name_file_path))
        elif (var.type).lower() == 'password':
            var.generated_value = get_random_password(8)
        elif (var.type).lower() == 'port':
            var.generated_value = get_random_port(var)
        elif (var.type).lower() == 'ip' or (var.type).lower() == 'ipv4':
            var.generated_value = get_random_IP(var)
    return variables


'''
Help function to map each object to tuple key value.

Parameters
    ----------
    variables : list
        list of Variable objects

Returns
    ------- 
    dict 
        dictionary with name of the variable as key and generate value as value

'''
def map_var_list_to_dict(var_list):
    var_dict = dict()
    for var in var_list:
        var_dict[var.name] = var.generated_value
    return var_dict


'''
Main function to generate random values in dependece on setted restrictions.

Parameters
    ----------
    variables : list
        list of Variable objects

Returns
    ------- 
    dict 
        dictionary with name of the variable as key and generate value as value

'''
def generate(variable_list):
    list_of_generated_objects = generate_randomized_arg(variable_list)
    return map_var_list_to_dict(list_of_generated_objects)
