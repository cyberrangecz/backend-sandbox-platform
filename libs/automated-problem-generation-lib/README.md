# Welcome to generator library's documentation!

---

Generator library is a Python package that generates random data within set restrictions for you. When you need to generate random individual values into game for various players, this library is for you.

---

## Compatibility

It supports only Python 3.6, and the above Python versions may work as well, however it is not guaranteed. 

## Basic Usage

Install with pip:

```pip install generator.whl```

Use `generator.var_parser()` to parse input variables in the file and then use `generator.var_generator()` to generate directory filled with generated data.

```
from generator.var_generator import generate
from generator.var_parser import parser_var_file

with open("input.yml") as file:
        seed = 1234
        variable_list = parser_var_file(file)
        print(file.read())
        
        # generated_text:
        #    type: text
        #
        #generated_password:
        #    type: password
        #
        #generated_username:
        #    type: username
        #
        #generated_port:
        #    type: port
        
        print(generate(variable_list, seed))
        
        # {'generated_text': 'I always did something i was not ready to do. I think that’s how you grow. When there’s that moment of ', 'generated_password': '6SBI1LEz', 'generated_username': 'persona', 'generated_port': '35608'}

        
```

How to use generator library with CTFd personal plugin?
---
---
To use generated values with individual flags within CTFd, try our generator application that manages all necessary for you. Just follow `README.md` instructions from *Kosc-automated-problem-generation-app*.

How it works
---
---
Generator pkg consists of `var_generator.py` where all required logical functions to generation process can be found, `var_parser.py` which function loads and processes the input data and finally `var_object.py` which stores class `Variable`. 

`var_generator.py` 
---
has function `generator(list_of_Variable)` with one argument that is list filled with `Variable` object. The result is that each object of `Variable` has been filled attribute `generated_value` with selected restrictions.

`var_parser.py` 
---
has function `parser_var_file(var_file)` that reads all input data from `var_file` the Python file object and fill them into `Variable` objects that are return as list. 

`var_object.py` 
---
has constructor with all possible attributes that has impact on generation process. The only mandatory arguments are `name` and `type`. 

Supported variable types:
---
---

* **username** - randomly chosen username
* **password** - randomly generated characters
* **port**     - randomly generated number
* **text**     - randomly chosen sentence

## How should variable in input file look like?

---
---
You have to create one input file to set minimal or maximal generated values, set prohibited values for each generated variable.

| keyword / type: | username | password | text | port | IP |
| -------------   |:-----: | :-----:| :----:| :----:| :-----:|
| ***type***      | ✓      | ✓      | ✓     | ✓     | ✓     |
| min             | -      | -      | -     | ✓     | ✓     |
| max             | -      | -      | -     | ✓     | ✓     |
| prohibited      | ✓      | ✓      | -     | ✓     | ✓     |
| challenge_id    | ✓      | ✓      | ✓     | ✓     | ✓     |
| length          | ✓      | ✓      | -     | -     | -     |

***bold*** - required attribute

normal     - optional attribute

### Structure of input file:

      <variable_name>:
         - type: <variable_type>  
            min: <value>
            max: <value>
            length: <value>
            prohibited: [<value>,<value>,...]
            challenge_id: <int>  
    
      <variable_name>:
            ...

#### Attributes:
- min - Minimal value that still can be generated
- max - Maximal value that still can be generated
- length - Number variable of characters   
- prohibited - List of values that are excluded
- challenge_id - ID of the challenge where will be generated flag uploaded 

> NOTE: challenge_id - works only with generator application (Kosc-automated-problem-generation-app)