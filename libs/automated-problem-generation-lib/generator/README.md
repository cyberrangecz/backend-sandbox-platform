Structure:
---
---

`__init__.py`
---
compulsary file for every package

`names.txt`
---
file with list of names to choose from

`passwords.txt`
---
file with list of passwords to choose from

`README.md`
---
this file

`var_generator.py`
---
logic of generation with help functions:

get_random_name(name_file)
---

return value - string one of the name in text file

argument - text file

get_random_password(length)
---

return value - string random letters and digits with exact length

argument - length of the password

get_random_port(var_obj)
---

return value - int 

argument - object type of Variable with possible restrictions

get_random_IP(var_obj)
---
return value - string IPv4

argument - object type of Variable with possible restrictions

get_cwd(file)
---
return value - string curent working directory

argument - file


generate_randomized_arg(variables)
---
return value - list of Variable objects

argument - list of Variable objects

functions fill each Variable object's attribute `generated_value` from argument with generated value


map_var_list_to_dict(var_list)
---
return value - dictionary with name of the variable as key and generate value as value

argument - list of Variable objects

generate(variable_list)
---
return value - list of Variable objects

argument - dictionary with name of the variable as key and generate value as value


`var_object.py`
---
class Variable
