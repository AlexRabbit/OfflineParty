import os
import sys
import json
import requests
import argparse
import html2text
import get_favorites
from tqdm import tqdm
from bs4 import BeautifulSoup
from pathvalidate import sanitize_filename
from user_search import main as user_search
from json_handling import lookup_and_save_user as save_artist_json
from discord_download import scrape_discord_server as discord_download


# Map Kemono artist IDs to their names
def create_artist_id_to_name_mapping(data):
    if isinstance(data, dict):
        if "id" in data and "name" in data:
            return {data["id"]: data["name"].capitalize()}
        else:
            return {}
    elif isinstance(data, list):
        return {item["id"]: item["name"].capitalize() for item in data if isinstance(item, dict) and "id" in item and "name" in item}
    else:
        return {}  # Return an empty dictionary for unsupported data types


def get_post_folder_name(post):
    # Get the post title and strip any whitespace or newline characters
    title = post.get('title', '').strip()

    # Get the published date or fallback to the added date
    date = post.get('published') or post.get('added')

    # If there's no title, use the post's id
    if not title:
        title = post.get('id', 'Unknown')

    # If there's a date, append it to the title
    if date:
        return sanitize_filename(f"{title}_{date}")
    else:
        return sanitize_filename(title)


def sanitize_attachment_name(name):
    # Remove any URL components
    name = name.replace("https://", "").replace("http://", "")
    # Further sanitize the name to remove invalid characters
    return sanitize_filename(name)


def get_with_retry_and_fallback(url, retries=3,
                                fallback_tld=".su",
                                stream=False):
    for i in range(retries):
        try:
            response = requests.get(url, stream=stream)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException:
            print(f"Failed to get {url}, attempt {i + 1}")
            if i == retries - 1:
                fallback_url = url.replace(".party", fallback_tld)
                print(f"Retrying with fallback URL: {fallback_url}")
                for j in range(retries):
                    try:
                        fallback_response = requests.get(fallback_url,
                                                         stream=stream)
                        fallback_response.raise_for_status()
                        return fallback_response
                    except requests.exceptions.RequestException:
                        print(f"Failed to get {fallback_url}, attempt {j + 1}")
                        if j == retries - 1:
                            print(f"Failed to download {fallback_url}",
                                  'logging to errors.txt')
                            with open("errors.txt", 'a') as error_file:
                                error_file.write(f"{fallback_url}\\n")


def download_file(url, folder_name, file_name, artist_url):
    folder_path = os.path.join(folder_name, file_name)
    temp_folder_path = os.path.join(folder_name, file_name + ".temp")

    # If a temporary file exists, remove it to restart the download
    if os.path.exists(temp_folder_path):
        os.remove(temp_folder_path)

    # If the final file exists, skip the download
    if os.path.exists(folder_path):
        print(f"Skipping download: {file_name} already exists")
        return

    response = get_with_retry_and_fallback(url, stream=True)
    if response and response.status_code == 200:
        total_size_in_bytes = int(response.headers.get('content-length', 0))
        progress_bar = tqdm(total=total_size_in_bytes,
                            unit='iB',
                            unit_scale=True,
                            leave=False)

        # Use a temporary file for the download process
        with open(temp_folder_path, 'wb') as f:
            for data in response.iter_content(1024):
                progress_bar.update(len(data))
                f.write(data)

        progress_bar.close()

        # Rename the temporary file to the final file name
        os.rename(temp_folder_path, folder_path)

        if total_size_in_bytes != 0 and progress_bar.n != total_size_in_bytes:
            print("ERROR, something went wrong")

        os.system('cls' if os.name == 'nt' else 'clear')  # Clear the console
        sys.stdout.write("\033[F")  # Move the cursor to the previous line
        sys.stdout.write("\033[K")  # Clear the line
        print(f"Downloading files from {artist_url}:")
        print(f"Downloading: {file_name}")


def run_with_base_url(url_list, data, json_file):
    # print("------------------- DATA ---------------\n", data)
    # print("------------------- URL LIST ---------------\n", url_list)
    print(f"Data type: {type(data)}")

    processed_users = set()
    current_artist = None
    current_artist_url = None

    previous_url = None  # Initialize a variable to keep track of the previous URL
    previous_artist_id = None  # Initialize a variable to keep track of the previous artist ID

    try:
        for i, url in enumerate(tqdm(url_list, desc="Downloading pages...")):
            url_parts = url.split("/")
            if len(url_parts) < 7:
                print(f"Unexpected URL structure: {url}")
                continue

            domain = url_parts[2].split(".")[0].capitalize()
            service = url_parts[5].capitalize()
            artist_id = url_parts[7].split("?")[0]
            artist_name = None

            print(f"Checking data type again: {type(data)}")

            artist_name = data.get(artist_id, None)  # Look up artist_id directly in data

            if artist_name:
                artist_name = artist_name.capitalize()
            else:
                print(f"Artist ID {artist_id} not found in data.")
                continue

            if service == 'Discord':
                discord_download(artist_id)
                continue

            artists_folder = "Creators"
            domain_folder = os.path.join(artists_folder, domain)
            artist_folder = os.path.join(domain_folder, (sanitize_filename(artist_name)))
            platform_folder = os.path.join(artist_folder, sanitize_filename(service))

            os.makedirs(platform_folder, exist_ok=True)

            response = get_with_retry_and_fallback(url)
            response_data = json.loads(response.text)

            for post_num, post in enumerate(response_data, start=1):
                post_folder_name = get_post_folder_name(post)
                post_folder_name = sanitize_filename(post_folder_name)
                post_folder_path = os.path.join(platform_folder, post_folder_name)
                os.makedirs(post_folder_path, exist_ok=True)

                base_url = "/".join(url.split("/")[:3])  # Extract the base URL

                for attachment in post.get('attachments', []):
                    attachment_url = base_url + attachment.get('path', '')
                    # Sanitize the attachment name
                    attachment_name = sanitize_attachment_name(attachment.get('name', ''))
                    if attachment_url and attachment_name:
                        download_file(attachment_url, post_folder_path, attachment_name, url)

                file_info = post.get('file')
                if file_info and 'name' in file_info and 'path' in file_info:
                    file_url = base_url + file_info['path']
                    # Sanitize the file name
                    file_name = sanitize_attachment_name(file_info['name'])
                    if file_url and file_name:
                        download_file(file_url, post_folder_path, file_name, url)

                content = post.get('content', '')
                post_url = f"{base_url}/{service.lower()}/user/{artist_id.lower()}/post/{post['id']}"
                save_content_to_txt(post_folder_path, content, post.get('embed', {}), post_url)

                # Extract the username from the URL
                username = url.split('/')[-1].split('?')[0]

                # Check if the username is not in the set of processed users
                if username not in processed_users:
                    if artist_name != current_artist:
                        current_artist_url = url
                    else:
                        current_artist = artist_name

                    processed_users.add(username)

            if previous_url and (artist_id != previous_artist_id or i == len(url_list) - 1):
                print("Saving artist to JSON")
                save_artist_json(previous_url)

            previous_url = url
            previous_artist_id = artist_id

    except requests.exceptions.RequestException:
        return False

    return True


def save_content_to_txt(folder_name, content, embed, post_url):
    folder_path = os.path.join(folder_name, "content.txt")
    comment_section = ""

    try:
        # Fetch the HTML content from the post_url
        response = requests.get(post_url)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract comments
        comments = soup.find_all('article', class_='comment')
        comment_list = []

        for comment in comments:
            user = comment.find('a', class_='comment__name').text
            message = comment.find('p', class_='comment__message').text
            timestamp = comment.find('time', class_='timestamp').text

            formatted_comment = f"{user} - {message} - {timestamp}"
            comment_list.append(formatted_comment)

        # Join comments with line breaks
        comment_section = '\n'.join(comment_list)

    except Exception as e:
        print(f"Error fetching comments from {post_url}: {e}")

    with open(folder_path, 'w', encoding='utf-8') as f:
        f.write("[POST URL]\n")
        f.write(f"{post_url}\n\n")
        f.write("[CONTENT]\n")
        f.write(html2text.html2text(content))
        f.write("\n")

        if embed:
            f.write("[EMBED]\n")
            for key, value in embed.items():
                f.write(f"{key.capitalize()}: {value}\n")
            f.write("\n")

        if comment_section:
            f.write("[COMMENTS]\n")
            f.write(comment_section)
            f.write("\n")


def main(option):
    options = [option] if option != "both" else ["kemono", "coomer"]
    url_list = []

    for option in options:
        api_pages, json_data = get_favorites.main(option)
        url_list.extend(api_pages)
        artist_id_to_name = create_artist_id_to_name_mapping(json_data)
        run_with_base_url(url_list, artist_id_to_name, json_data)
        
def download_for_multiple_users(username_file):
    with open(username_file, 'r') as file:
        usernames = [line.strip() for line in file.readlines()]

    for username in usernames:
        print(f"Downloading content for {username}")
        url, username, json_data = user_search(username)
        artist_id_to_name = create_artist_id_to_name_mapping(json_data)
        run_with_base_url(url, artist_id_to_name, json_data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download data from websites.")
    parser.add_argument('-f', '--file', type=str, metavar='USERNAME_FILE',
                        help="Path to the text file containing usernames")
    args = parser.parse_args()

    if args.file:
        download_for_multiple_users(args.file)
    else:
        print("Please specify a username file using the -f or --file option.")


def delete_json_file(filename):
    # Check if file exists
    if os.path.exists(filename):
        try:
            os.remove(filename)
            print(f"{filename} removed successfully")
        except Exception:
            print(f"Unable to delete {filename}")
            print(Exception)
    else:
        print(f"No file found with the name {filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download data from websites.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-k',
                       '--kemono',
                       action='store_true',
                       help="Download data from kemono")

    group.add_argument('-c',
                       '--coomer',
                       action='store_true',
                       help="Download data from coomer")

    group.add_argument('-b',
                       '--both',
                       action='store_true',
                       help="Download data from both sites")
    group.add_argument('-u',
                        '--user',
                        type=str,
                        metavar='USERNAME',
                        help="Only download posts from a specific user")
    parser.add_argument('-r',
                        '--reset',
                        action='store_true',
                        help="Reset JSON file for selected flag")

    args = parser.parse_args()

    if args.kemono:
        if args.reset:
            delete_json_file('Config/kemono_favorites.json')
        main("kemono")
    elif args.coomer:
        if args.reset:
            delete_json_file('Config/coomer_favorites.json')
        main("coomer")

    elif args.user:
        # user = args.user if args.user else str(input("Please type the name of the creator: "))
        user = args.user or str(input("Please type the name of the creator: "))
        url, username, json_data,  = user_search(user)
        # DEBUG print("-------------------URL----------------------\n",url)
        # DEBUG print("-------------------Username----------------------\n",username)
        # print("-------------------json_file_path----------------------\n",json_file_path)
        artist_id_to_name = create_artist_id_to_name_mapping(json_data)
        run_with_base_url(url, artist_id_to_name, json_data)
    elif args.both:
        if args.reset:
            delete_json_file('Config/kemono_favorites.json')
            delete_json_file('Config/coomer_favorites.json')
        main("both")
