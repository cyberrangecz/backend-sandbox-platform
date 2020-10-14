import requests,sys

def get_package_id():
    headers = {'PRIVATE-TOKEN': sys.argv[1], }
    return requests.get('https://gitlab.fi.muni.cz/api/v4/projects/14820/packages', headers=headers).json()[-1]["id"]

def delete_package():

    headers = {
        'PRIVATE-TOKEN': sys.argv[1],
    }
    if 47 == get_package_id():
        return
    response = requests.delete('https://gitlab.fi.muni.cz/api/v4/projects/14820/packages/'+str(get_package_id()), headers=headers)

delete_package()