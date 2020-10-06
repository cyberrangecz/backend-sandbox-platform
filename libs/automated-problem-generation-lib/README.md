How to use generator?
---
---
To test generator follow README.md instructions from Kosc-automated-problem-generation-app.

How it works
---
---
generator pkg consists from `var_generator.py` where are all logical functions and `var_object.py` where is stored class `Variable`. 

`var_object.py` 
---
has constructor with all possible attributes that has impact on generation proces. The only mandarory arguments are `name` and `type`. 

`var_generator.py` 
---
has functions `generator(list_of_Variable)` with one argument that is list fill with `Variable` object. The result is that each object of `Variable` has been filled attribute `generated_value` with selected restrictions.

Application
---
---
* in cases when you need to randomize your configuration but with some restrictions to generated value

