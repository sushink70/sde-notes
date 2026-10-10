class Solution {
public:
    void solve(vector<vector<char>>& board) {
	// find O in the edges
	// run bfs on those O to make sure that they are not surrounded
	// mark the O found in bfs as #
	// Then go through each element
	// if element is # convert to O
	// if element is O convert to X
        // i,j i=0 j = 0...m-1 i = n-1 j = 0...m-1
        // i,j j=0 i = 1...n-2 j = m-1 i = 1...n-2
        int r = board.size();
        int c = board[0].size();

        int i = 0;
        for (int j =0;j < c;j++){
            if (board[i][j]=='O'){
                bfs(board,i,j);
            }
        }
         i = r-1;
        for (int j =0;j <c;j++){
            if ( board[i][j]=='O'){ // 3,1
                bfs(board,i,j);
            }
        }
         i = 0;
        for (int j =1;j <r-1;j++){
            if ( board[j][i]=='O'){
                bfs(board,j,i);
            }
        }
         i = c-1;
        for (int j =1;j <r-1;j++){
            if (board[j][i]=='O'){
                bfs(board,j,i);
            }
        }

        for(int a=0;a<r;a++){
            for (int b=0;b<c;b++){
                if (board[a][b]=='#'){
                    board[a][b]='O';
                }else if(board[a][b]=='O'){
                    board[a][b]='X';
                } 
            }
        }
            
    }
    void bfs(vector<vector<char>>& board, int i, int j){
        // Need to work on DFS

        queue<pair<int,int>> q;
        q.push({i,j});
        board[i][j] = '#';
        while (!q.empty()){ // [3,1]
            pair<int,int> p = q.front();
            q.pop();
            check(board,q,p.first+1,p.second);
            check(board,q,p.first-1,p.second);
            check(board,q,p.first,p.second+1);
            check(board,q,p.first,p.second-1);
        }
    }

    void check(vector<vector<char>>& board,queue<pair<int,int>>& q, int i, int j){
        if (i<board.size() && j<board[0].size() && i>=0 && j>=0 && board[i][j]=='O'){
            board[i][j]='#';
            q.push({i,j});
        }
    }
    
};